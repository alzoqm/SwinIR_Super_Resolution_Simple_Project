# SwinIR_Super_Resolution_Simple_Project
사용한 모델 논문: https://arxiv.org/abs/2108.10257<br>
공식 코드(파이토치): https://github.com/JingyunLiang/SwinIR<br>
모델 코드 설명: https://github.com/alzoqm/transformer_model/tree/main/models/swinIR
## Train Dataset 및 Model Parameter
### Train Dataset
kaggle danbooru2020 dataset: https://www.kaggle.com/muoncollider/danbooru2020<br>
* danbooru dataset의 경우 한 폴더에 모든 파일이 있는 것이 아닌 1, 2, 3과 같이 분할된 폴더에 2000~3000개씩 분할되어 있음
* 이러한 점을 향후 ram 절약을 위해 활용함. (Training Method 참조)

|parameter name|value|parameter name|value|
|:---:|:--:|:--:|:--:|
|IMG_SIZE|64|PATCH_SIZE|1|
|IN_CHANS|3|EMB_SIZE|180|
|DEPTHS|[6, 6, 6, 6]|NUM_HEADS|[6, 6, 6, 6]|
|WINDOW_SIZE|4|MLP_RATIO|4|
|QKV_BIAS|True|DROP_RATE|0.1|
|ATTN_DROP_RATE|0.1|DROP_PATH_RATE|0.1|
|APE|False|PATCH_NORM|True|
|UPSCALE|2|IMG_RANGE|255|
|RESI_CONNECTION|'3conv'|BATCH_SIZE|8|

## Training Method
* colab pro의 TPU 버전을 사용하여 학습하였기 때문에 학습에 제한이 있음<br>
1. 학습하기 위해 마련한 모든 데이터를 한번에 pipeline에 넣을 경우 ram이 버티지를 못함<br>
2. 큰 이미지를 넣을 경우 vram 역시 한계가 있음
* 1번의 문제를 해결하고자, 모든 데이터를 한번에 pipeline에 넣기 보단, 2000~3000개씩 넣어 학습을 진행함(epoch값에 해당하는 이미지만 불러옴)
* 2번의 문제를 해결하기 위해, 사이즈가 512x512 데이터를 64 x 64로 분할하여 학습을 진행함.(new_swinir_sr 함수 image_slice참조)

## Result

|Low Resolution|Output High Resolution(2x)|
|:--------:|:---------:|
|<img src="https://user-images.githubusercontent.com/70330480/152933821-72a75ec0-1f62-4434-88ac-e026db2bb683.png" width="100%" height="100%">|<img src="https://user-images.githubusercontent.com/70330480/152933888-7f6961d3-9712-40a7-b383-fc5da89f6650.png" width="100%" height="100%">

### 확대 이미지
|waifu2x(anotherSRmodel)|Low Resolution|SwinIR High Resolution(2x)|
|:--------:|:---------:|:---------|
|<img src="https://user-images.githubusercontent.com/70330480/152936754-00a6100e-c658-4751-8b75-014bb1fa7b48.png" width="600">|<img src="https://user-images.githubusercontent.com/70330480/152936831-42807295-5cbf-489e-9612-e5a02c3dfd9b.png" width="900">|<img src="https://user-images.githubusercontent.com/70330480/152936872-2b5482cd-cb39-44fe-bd01-c787b4298864.png" width="800">|

## 문제점
모델을 학습할 때는 모델의 크기가 너무 커서 64x64로 이미지를 잘라서 넣을 수 밖에 없는 문제가 발생함  
이 문제를 핵결하기 위한 방법을 찾는 것은 또 다른 과제  
현재 학습시 mixed-precision 적용 및 배포를 위한 파라미터 양자화 기법 공부 중...
