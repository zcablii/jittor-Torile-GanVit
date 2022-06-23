"""
Copyright (C) 2019 NVIDIA Corporation.  All rights reserved.
Licensed under the CC BY-NC-SA 4.0 license (https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode).
"""
import jittor as jt
from jittor import init
from jittor import nn
import sys
from collections import OrderedDict
from options.train_options import TrainOptions
import data
from util.iter_counter import IterationCounter
from util.visualizer import Visualizer
from trainers.pix2pix_trainer import Pix2PixTrainer
import os
from tensorboardX import SummaryWriter
os.environ['CUDA_LAUNCH_BLOCKING'] = '1'
jt.flags.use_cuda = 1

opt = TrainOptions().parse()
if opt.USE_AMP:
    jt.flags.auto_mixed_precision_level = 5
writer = SummaryWriter(os.path.join(opt.checkpoints_dir, opt.name))
print(' '.join(sys.argv))
dataloader = data.create_dataloader(opt)
trainer = Pix2PixTrainer(opt)
iter_counter = IterationCounter(opt, len(dataloader))
visualizer = Visualizer(opt)
print_sample_num = 8
for epoch in iter_counter.training_epochs():
    iter_counter.record_epoch_start(epoch)
    for (i, data_i) in enumerate(dataloader, start=iter_counter.epoch_iter):
        # print('iter ',i)
        iter_counter.record_one_iteration()
        # print('data_i is ok', data_i['label'][0][0][0])
        if ((i % opt.D_steps_per_G) == 0):
            trainer.run_generator_one_step(data_i, epoch)
        # print('data_i is ok', data_i['label'])
        trainer.run_discriminator_one_step(data_i, epoch)
        losses = trainer.get_latest_losses()
        loss_names = ['GAN', 'GAN_Feat', 'VGG', 'D_Fake', 'D_real']
        ct = 0
        for (k, v) in losses.items():
            v = v.mean().float()
            writer.add_scalar(loss_names[ct], v.item(), (epoch - 1) * len(dataloader) + i)
            ct += 1
        if jt.rank==0 and iter_counter.needs_printing():
            losses = trainer.get_latest_losses()
            visualizer.print_current_errors(epoch, iter_counter.epoch_iter, losses, iter_counter.time_per_iter)
            visualizer.plot_current_errors(losses, iter_counter.total_steps_so_far)
        if jt.rank==0 and iter_counter.needs_displaying():
            visuals = OrderedDict([('synthesized_image', trainer.get_latest_generated()[:print_sample_num]), ('real_image', data_i['image'][:print_sample_num])])
            visualizer.display_current_results(visuals, epoch, iter_counter.total_steps_so_far)
        if jt.rank==0 and iter_counter.needs_saving():
            print(('saving the latest model (epoch %d, total_steps %d)' % (epoch, iter_counter.total_steps_so_far)))
            trainer.save('latest')
            iter_counter.record_current_iter()
        jt.sync_all(True)
    trainer.update_learning_rate(epoch)
    iter_counter.record_epoch_end()

    if jt.rank==0 and (epoch % opt.save_epoch_freq == 0 or \
       epoch == iter_counter.total_epochs):
        print('saving the model at the end of epoch %d, iters %d' %
              (epoch, iter_counter.total_steps_so_far))
        trainer.save('latest')
        trainer.save(epoch)
print('Training was successfully finished.')
