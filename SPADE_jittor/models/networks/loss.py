"""
Copyright (C) 2019 NVIDIA Corporation.  All rights reserved.
Licensed under the CC BY-NC-SA 4.0 license (https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode).
"""
import jittor as jt
from jittor import init
from jittor import nn
from models.networks.architecture import VGG19, InceptionV3


# Defines the GAN loss which uses either LSGAN or the regular GAN.
# When LSGAN is used, it is basically same as MSELoss,
# but it abstracts away the need to create the target label tensor
# that has the same size as the input
class GANLoss(nn.Module):
    def __init__(self, gan_mode, target_real_label=1.0, target_fake_label=0.0,
                 tensor=jt.float16, opt=None):
        super(GANLoss, self).__init__()
        self.real_label = target_real_label
        self.fake_label = target_fake_label
        self.real_label_tensor = None
        self.fake_label_tensor = None
        self.zero_tensor = None
        self.Tensor = tensor
        self.gan_mode = gan_mode
        self.opt = opt
        if gan_mode == 'ls':
            pass
        elif gan_mode == 'original':
            pass
        elif gan_mode == 'w':
            pass
        elif gan_mode == 'hinge':
            pass
        else:
            raise ValueError('Unexpected gan_mode {}'.format(gan_mode))

    def get_target_tensor(self, input, target_is_real):
        if target_is_real:
            if self.real_label_tensor is None:
                self.real_label_tensor = self.Tensor(self.real_label)
                self.real_label_tensor.stop_grad()
            return self.real_label_tensor.expand_as(input)
        else:
            if self.fake_label_tensor is None:
                self.fake_label_tensor = self.Tensor(self.fake_label)
                self.fake_label_tensor.stop_grad()
            return self.fake_label_tensor.expand_as(input)

    def get_zero_tensor(self, input):
        if self.zero_tensor is None:
            self.zero_tensor = self.Tensor(0)
            self.zero_tensor.stop_grad()
        return self.zero_tensor.expand_as(input)

    def loss(self, input, target_is_real, for_discriminator=True):
        if self.gan_mode == 'original':  # cross entropy loss
            target_tensor = self.get_target_tensor(input, target_is_real)
            loss = nn.binary_cross_entropy_with_logits(input, target_tensor)
            return loss
        elif self.gan_mode == 'ls':
            target_tensor = self.get_target_tensor(input, target_is_real)
            return nn.mse_loss(input, target_tensor)
        elif self.gan_mode == 'hinge':
            if for_discriminator:
                if target_is_real:
                    minval = jt.minimum(input - 1, self.get_zero_tensor(input))
                    loss = -jt.mean(minval)
                else:
                    minval = jt.minimum(-input - 1, self.get_zero_tensor(input))
                    loss = -jt.mean(minval)
            else:
                assert target_is_real, "The generator's hinge loss must be aiming for real"
                loss = -jt.mean(input)
            return loss
        else:
            # wgan
            if target_is_real:
                return -input.mean()
            else:
                return input.mean()

    def __call__(self, input, target_is_real, for_discriminator=True):
        # computing loss is a bit complicated because |input| may not be
        # a tensor, but list of tensors in case of multiscale discriminator
        if isinstance(input, list):
            loss = 0
            for pred_i in input:
                if isinstance(pred_i, list):
                    pred_i = pred_i[-1]
                loss_tensor = self.loss(pred_i, target_is_real, for_discriminator)
                bs = 1 if len(loss_tensor.size()) == 0 else loss_tensor.size(0)
                new_loss = jt.mean(loss_tensor.view(bs, -1), dim=1)
                loss += new_loss
            return loss / len(input)
        else:
            return self.loss(input, target_is_real, for_discriminator)


# Perceptual loss that uses a pretrained VGG network
class VGGLoss(nn.Module):
    def __init__(self, gpu_ids):
        super(VGGLoss, self).__init__()
        self.vgg = VGG19()
        self.weights = [1.0 / 32, 1.0 / 16, 1.0 / 8, 1.0 / 4, 1.0]

    def execute(self, x, y):
        x_vgg, y_vgg = self.vgg(x), self.vgg(y)
        loss = 0
        for i in range(len(x_vgg)):
            l1_loss = jt.abs(jt.float32(x_vgg[i] - y_vgg[i].detach())).mean().float_auto()
            loss += self.weights[i] * l1_loss
        return loss

class InceptionLoss(nn.Module):
    def __init__(self, gpu_ids):
        super(InceptionLoss, self).__init__()
        self.inception = InceptionV3()
        # self.weights = [1.0 / 32, 1.0 / 16, 1.0 / 8, 1.0 / 4, 1.0]
        self.weights = [1.0 / 16, 1.0 / 8, 1.0 / 4, 1.0]

    def execute(self, x, y):
        x_inception, y_inception = self.inception(x), self.inception(y)
        loss = 0
        for i in range(len(x_inception)):
            l1_loss = jt.abs(jt.float32(x_inception[i] - y_inception[i].detach())).mean().float_auto()
            loss += self.weights[i] * l1_loss
        return loss

# KL Divergence loss used in VAE with an image encoder
class KLDLoss(nn.Module):
    def execute(self, mu, logvar):
        return -0.5 * jt.sum(1 + logvar - mu.pow(2) - logvar.exp())
