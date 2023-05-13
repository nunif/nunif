nvidia-smi
sudo apt install git-core libmagickwand-dev libsnappy-dev libraqm-dev python-is-python3 python3 python3-pip python3-dev python3-tk -y
git clone https://github.com/nagadomi/nunif.git
cd nunif
pip3 install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu121
pip3 install -r requirements.txt
mkdir data
mkdir data/dataset
mkdir data/dataset/train
mkdir data/waifu2x
mkdir data/dataset/waifu2x
mkdir models
mkdir models/waifu2x_mymodel
