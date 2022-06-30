# Semantic Image Synthesis with PG-SPADE

![1656585143029](image/README/1656585143029.png)

## Installation

Clone this repo.

```bash
git clone https://github.com/zcablii/jittor-Torile-GanVit.git
cd jittor-Torile-GanVit-main/
```

This code requires python 3+ and PyTorch 1.8(for pytorch version implementation) or Jittor 1.3 (for Jittor version implementaion). Please install dependencies by

```bash
sudo apt install python3.7-dev libomp-dev  
python3.7 -m pip install jittor  
pip install -r requirements.txt
```

## Dataset Preparation

For COCO-Stuff, Cityscapes or ADE20K, the datasets must be downloaded beforehand. Please download them on the respective webpages. In the case of COCO-stuff, we put a few sample images in this code repo.

**Preparing COCO-Stuff Dataset**. The dataset can be downloaded [here](https://github.com/nightrome/cocostuff). In particular, you will need to download train2017.zip, val2017.zip, stuffthingmaps_trainval2017.zip, and annotations_trainval2017.zip. The images, labels, and instance maps should be arranged in the same directory structure as in `datasets/coco_stuff/`. In particular, we used an instance map that combines both the boundaries of "things instance map" and "stuff label map". To do this, we used a simple script `datasets/coco_generate_instance_map.py`. Please install `pycocotools` using `pip install pycocotools` and refer to the script to generate instance maps.

**Preparing ADE20K Dataset**. The dataset can be downloaded [here](http://data.csail.mit.edu/places/ADEchallenge/ADEChallengeData2016.zip), which is from [MIT Scene Parsing BenchMark](http://sceneparsing.csail.mit.edu/). After unzipping the datgaset, put the jpg image files `ADEChallengeData2016/images/` and png label files `ADEChallengeData2016/annotatoins/` in the same directory.

There are different modes to load images by specifying `--preprocess_mode` along with `--load_size`. `--crop_size`. There are options such as `resize_and_crop`, which resizes the images into square images of side length `load_size` and randomly crops to `crop_size`. `scale_shortside_and_crop` scales the image to have a short side of length `load_size` and crops to `crop_size` x `crop_size` square. To see all modes, please use `python train.py --help` and take a look at `data/base_dataset.py`. By default at the training phase, the images are randomly flipped horizontally. To prevent this use `--no_flip`.

## Generating Images Using Pretrained Model

Once the dataset is ready, the result images can be generated using pretrained models.

1. Download the tar of the pretrained models from the [Google Drive Folder](https://drive.google.com/file/d/12gvlTbMvUcJewQlSEaZdeb2CdOB-b8kQ/view?usp=sharing), save it in 'checkpoints/', and run

   ```
   cd checkpoints
   tar xvf checkpoints.tar.gz
   cd ../
   ```
2. Generate images using the pretrained model.

   ```bash
   python test.py --name [type]_pretrained --dataset_mode [dataset] --dataroot [path_to_dataset]
   ```

   `[type]_pretrained` is the directory name of the checkpoint file downloaded in Step 1, which should be one of `coco_pretrained`, `ade20k_pretrained`, and `cityscapes_pretrained`. `[dataset]` can be one of `coco`, `ade20k`, and `cityscapes`, and `[path_to_dataset]`, is the path to the dataset. If you are running on CPU mode, append `--gpu_ids -1`.
3. The outputs images are stored at `./results/[type]_pretrained/` by default. You can view them using the autogenerated HTML file in the directory.

## Generating Landscape Image using GauGAN

In the paper and the demo video, we showed GauGAN, our interactive app that generates realistic landscape images from the layout users draw. The model was trained on landscape images scraped from Flickr.com. We released an online demo that has the same features. Please visit [https://www.nvidia.com/en-us/research/ai-playground/](https://www.nvidia.com/en-us/research/ai-playground/). The model weights are not released.

## Training New Models

New models can be trained with the following commands.

1. Prepare dataset. To train on the datasets shown in the paper, you can download the datasets and use `--dataset_mode` option, which will choose which subclass of `BaseDataset` is loaded. For custom datasets, the easiest way is to use `./data/custom_dataset.py` by specifying the option `--dataset_mode custom`, along with `--label_dir [path_to_labels] --image_dir [path_to_images]`. You also need to specify options such as `--label_nc` for the number of label classes in the dataset, `--contain_dontcare_label` to specify whether it has an unknown label, or `--no_instance` to denote the dataset doesn't have instance maps.
2. Train.

```bash
# To train on the Facades or COCO dataset, for example.
python train.py --name [experiment_name] --dataset_mode facades --dataroot [path_to_facades_dataset]
python train.py --name [experiment_name] --dataset_mode coco --dataroot [path_to_coco_dataset]

# To train on your own custom dataset
python train.py --name [experiment_name] --dataset_mode custom --label_dir [path_to_labels] -- image_dir [path_to_images] --label_nc [num_labels]
```

There are many options you can specify. Please use `python train.py --help`. The specified options are printed to the console. To specify the number of GPUs to utilize, use `--gpu_ids`. If you want to use the second and third GPUs for example, use `--gpu_ids 1,2`.

To log training, use `--tf_log` for Tensorboard. The logs are stored at `[checkpoints_dir]/[name]/logs`.

## Testing

Testing is similar to testing pretrained models.

```bash
python test.py --name [name_of_experiment] --dataset_mode [dataset_mode] --dataroot [path_to_dataset]
```

Use `--results_dir` to specify the output directory. `--how_many` will specify the maximum number of images to generate. By default, it loads the latest checkpoint. It can be changed using `--which_epoch`.

## Code Structure

- `train.py`, `test.py`: the entry point for training and testing.
- `trainers/pix2pix_trainer.py`: harnesses and reports the progress of training.
- `models/pix2pix_model.py`: creates the networks, and compute the losses
- `models/networks/`: defines the architecture of all models
- `options/`: creates option lists using `argparse` package. More individuals are dynamically added in other files as well. Please see the section below.
- `data/`: defines the class for loading images and label maps.

## Options

This code repo contains many options. Some options belong to only one specific model, and some options have different default values depending on other options. To address this, the `BaseOption` class dynamically loads and sets options depending on what model, network, and datasets are used. This is done by calling the static method `modify_commandline_options` of various classes. It takes in the `parser` of `argparse` package and modifies the list of options. For example, since COCO-stuff dataset contains a special label "unknown", when COCO-stuff dataset is used, it sets `--contain_dontcare_label` automatically at `data/coco_dataset.py`. You can take a look at `def gather_options()` of `options/base_options.py`, or `models/network/__init__.py` to get a sense of how this works.

## VAE-Style Training with an Encoder For Style Control and Multi-Modal Outputs

To train our model along with an image encoder to enable multi-modal outputs as in Figure 15 of the [paper](https://arxiv.org/pdf/1903.07291.pdf), please use `--use_vae`. The model will create `netE` in addition to `netG` and `netD` and train with KL-Divergence loss.

## Acknowledgments

This code borrows heavily from SPADE.

## Run scripts

#### origin baseline 512pix with fp16 random crop from 640pix

CUDA_VISIBLE_DEVICES=0 python train.py --name='label2img' --batchSize=24 --label_dir='../data/train/gray_label' --image_dir='../data/train/imgs'

#### +pos emb at sematic label and generator intermediate features

CUDA_VISIBLE_DEVICES=0 python train.py --name='label2img' --batchSize=24 --label_dir='../data/train/gray_label' --image_dir='../data/train/imgs' --use_pos=True --use_pos_proj=True --use_interFeature_pos=True

#### Progressive growing training

CUDA_VISIBLE_DEVICES=0 python train.py --name='label2img' --label_dir='../data/train/gray_label' --image_dir='../data/train/imgs' --pg_strategy=1 --niter=150 --pg_niter=120 --niter_decay=30 --num_D=3

explain by example: if num_D=3, pg_niter=120, pg start with 128 pix, stabilise 128 for 30 eps, fade into 256 in 30 eps, stablilise 256 for 30 eps, fade into 512 for 30 eps. All other ongoing epochs are only for stabilising 512pix output. Add --lr=0.005 --pg_lr_decay=0.5 to use larger lr at lower reolution phase and lower lr at higher resolution phase.

#### 54.68 script

CUDA_VISIBLE_DEVICES=0 python train.py --name='label2img' --label_dir='../data/train/gray_label' --image_dir='../data/train/imgs' --pg_strategy=1 --niter=200 --pg_niter=180 --niter_decay=20 --num_D=4

#### PG+inception loss+diffaug+logger training

CUDA_VISIBLE_DEVICES=0 python train.py --name='label2img' --label_dir='../data/train/gray_label' --image_dir='../data/train/imgs' --niter=260 --pg_niter=180 --niter_decay=20 --pg_strategy=1 --num_D=4 --diff_aug='color,crop,translation' --inception_loss

#### PG Strategy1 pretrain (with 572 crop aug)

CUDA_VISIBLE_DEVICES=0 python train.py --name='label2img' --label_dir='../data/train/gray_label' --image_dir='../data/train/imgs' --niter=240 --pg_niter=240 --pg_strategy=1 --num_D=4

(add --reverse_map_D to reverse the mapping of D in PG stages: large D on small scale)

#### With above checkpoint, add inception loss, diff aug and spatial noise:

CUDA_VISIBLE_DEVICES=0 python train.py --name='label2img' --label_dir='../data/train/gray_label' --image_dir='../data/train/imgs' --niter=340 --pg_niter=240 --niter_decay=20 --pg_strategy=1 --num_D=4 --diff_aug='color,crop,translation' --inception_loss --use_seg_noise --continue_train --which_epoch=240

#### Test

CUDA_VISIBLE_DEVICES=0 python test.py --name='label2img' --batchSize=32 --label_dir='../data/eval/gray_label' --image_dir='../data/eval/gray_label'
