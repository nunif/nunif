import requests, uuid

with open("ww.txt", "r", encoding="utf-8") as file:
    url_list = file.readlines()

uu = []

for u in url_list:
    u = u.replace("\n", "")
    uu.append(f"{u}")

print("uu Done!")

for i in uu:
    id = str(uuid.uuid4())
    # 이미지 다운로드
    response = requests.get(i)
    # 이미지를 파일에 저장
    with open(f'../data/dataset/waifu2x/train/{id}.png', 'wb') as f:
        f.write(response.content)
    print(f"Save: ../data/dataset/waifu2x/train/{id}.png !")
