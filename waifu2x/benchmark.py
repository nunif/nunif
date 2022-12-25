import os
from os import path
import math
import torch
import argparse
import csv
from torchvision.transforms import functional as TF
from nunif.transforms import functional as NF
import nunif.transforms.image_magick as IM
from nunif.logger import logger
from nunif.utils.image_loader import ImageLoader
from tqdm import tqdm
import time


def load_files(txt):
    files = []
    with open(txt, "r") as f:
        reader = csv.reader(f)
        for row in reader:
            files.append(row[0])
    return files


def MSE(x1, x2, min_mse=1):
    mse = (x1 - x2).pow_(2).mean().clamp(min=min_mse).item()
    return mse


def MSE2PSNR(mse):
    return 10 * math.log10((255 * 255) / mse)


def add_jpeg_noise(x, args):
    if args.jpeg_yuv420:
        sampling_factor = IM.YUV420
    else:
        sampling_factor = IM.YUV444
    for i in range(args.jpeg_times):
        quality = args.jpeg_quality - i * args.jpeg_quality_down
        x = IM.jpeg_noise(x, sampling_factor, quality)
    return x


def make_input_waifu2x(x, args):
    if args.method == "scale":
        return IM.scale(x, 0.5, filter_type=args.filter)
    elif args.method == "noise":
        return add_jpeg_noise(x, args)
    elif args.method == "noise_scale":
        return add_jpeg_noise(IM.scale(x, 0.5, filter_type=args.filter), args)


def remove_border(x, border):
    return NF.crop(x, border, border, x.shape[1] - border, x.shape[2] - border)


def psnr256(x1, x2, color):
    assert (color in ("rgb", "y", "y_matlab"))
    assert (x1.shape == x2.shape)
    if color == "rgb":
        mse = MSE(NF.quantize256_f(x1), NF.quantize256_f(x2))
        psnr = MSE2PSNR(mse)
        return psnr, mse
    elif color == "y":
        mse = MSE(NF.quantize256_f(NF.rgb2y(x1)), NF.quantize256_f(NF.rgb2y(x2)))
        psnr = MSE2PSNR(mse)
        return psnr, mse
    elif color == "y_matlab":
        mse = MSE(NF.rgb2y_matlab(x1).float(), NF.rgb2y_matlab(x2).float())
        psnr = MSE2PSNR(mse)
        return psnr, mse


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=str, required=True, help="model dir")
    parser.add_argument("--noise-level", "-n", type=int, default=0, choices=[0, 1, 2, 3], help="noise level")
    parser.add_argument("--method", "-m", type=str, choices=["scale", "noise", "noise_scale"], default="scale", help="method")
    parser.add_argument("--color", type=str, choices=["rgb", "y", "y_matlab"], default="y_matlab", help="colorspace")
    parser.add_argument("--jpeg-quality", type=int, default=75, help="jpeg quality for noise/noise_scale")
    parser.add_argument("--jpeg-times", type=int, default=1, help="number of repetitions of jpeg compression")
    parser.add_argument("--jpeg-quality-down", type=int, default=5, help="value of jpeg quality that decreases every times")
    parser.add_argument("--jpeg-yuv420", action="store_true", help="use yuv420 jpeg")
    parser.add_argument("--filter", type=str, choices=["catrom", "box", "lanczos", "sinc", "triangle"], default="catrom",
                        help="downscaling filter for generate LR image")
    parser.add_argument("--baseline", action="store_true", help="use baseline score by image resize")
    parser.add_argument("--baseline-filter", type=str, default="catrom",
                        choices=["catrom", "box", "lanczos", "sinc", "triangle"], help="baseline filter for 2x")
    parser.add_argument("--border", type=int, default=0, help="border px removed from the result image")
    parser.add_argument("--gpu", "-g", type=int, nargs="+", default=[0], help="GPU device ids. -1 for CPU")
    parser.add_argument("--batch-size", type=int, default=4, help="minibatch_size")
    parser.add_argument("--tile-size", type=int, default=256, help="tile size for tiled render")
    parser.add_argument("--output", "-o", type=str, help="output file or directory")
    parser.add_argument("--input", "-i", type=str, required=True, help="input directory. (*.txt, *.csv) for image list")
    parser.add_argument("--tta", action="store_true", help="TTA mode")
    parser.add_argument("--amp", action="store_true", help="AMP mode")
    args = parser.parse_args()
    logger.debug(vars(args))

    return args


def main():
    from .utils import Waifu2x

    args = parse_args()
    ctx = Waifu2x(model_dir=args.model_dir, gpus=args.gpu)
    ctx.load_model(args.method, args.noise_level)

    if path.isdir(args.input):
        files = ImageLoader.listdir(args.input)
    elif path.splitext(args.input)[-1] in (".txt", ".csv"):
        files = load_files(args.input)
    else:
        raise ValueError("Unknown input format")

    loader = ImageLoader(files=files, max_queue_size=128, load_func_kwargs={"color": "rgb"})
    if args.output is not None:
        os.makedirs(args.output, exist_ok=True)
    with torch.no_grad():
        mse_sum = psnr_sum = time_sum = 0
        baseline_mse_sum = baseline_psnr_sum = baseline_time_sum = 0
        count = 0

        for im, meta in tqdm(loader, ncols=60):
            x = TF.to_tensor(im)
            groundtruth = NF.crop_mod(x, 4)
            x = make_input_waifu2x(groundtruth, args)
            t = time.time()
            z, _ = ctx.convert(x, None, args.method, args.noise_level,
                               args.tile_size, args.batch_size,
                               tta=args.tta, enable_amp=args.amp)
            time_sum += time.time() - t
            if args.border > 0:
                psnr, mse = psnr256(remove_border(groundtruth, args.border),
                                    remove_border(z, args.border), args.color)
            else:
                psnr, mse = psnr256(groundtruth, z, args.color)
            psnr_sum += psnr
            mse_sum += mse

            if args.baseline:
                t = time.time()
                if args.method in ("scale", "noise_scale"):
                    z = IM.scale(x, 2, filter_type=args.baseline_filter)
                else:
                    z = x
                baseline_time_sum += time.time() - t
                if args.border > 0:
                    psnr, mse = psnr256(remove_border(groundtruth, args.border),
                                        remove_border(z, args.border), args.color)
                else:
                    psnr, mse = psnr256(groundtruth, z, args.color)
                baseline_psnr_sum += psnr
                baseline_mse_sum += mse
            count += 1

        mpsnr = round(psnr_sum / count, 4)
        rmse = round(math.sqrt(mse_sum / count), 4)
        fps = round(count / time_sum, 4)
        print(f"* {args.model_dir}")
        print(f"PSNR: {mpsnr}, RMSE: {rmse}, time: {round(time_sum, 4)} ({fps} FPS)")
        if args.baseline:
            mpsnr = round(baseline_psnr_sum / count, 4)
            rmse = round(math.sqrt(baseline_mse_sum / count), 4)
            fps = round(count / baseline_time_sum, 4)
            if args.method == "scale":
                print(f"* {args.baseline_filter}")
            elif args.method == "noise_scale":
                print(f"* {args.baseline_filter}, jpeg")
            elif args.method == "noise":
                print("* jpeg")
            print(f"PSNR: {mpsnr}, RMSE: {rmse}, time: {round(baseline_time_sum, 4)} ({fps} FPS)")


if __name__ == "__main__":
    main()
