"""
Copyright (C) 2019 NVIDIA Corporation.  All rights reserved.
Licensed under the CC BY-NC-SA 4.0 license (https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode).
"""

import re
import numpy as np
import jittor as jt
from jittor import init
from jittor import nn
from jittor.nn import BatchNorm
# import torch.nn.utils.spectral_norm as spectral_norm

"""
Spectral Normalization from https://arxiv.org/abs/1802.05957
"""
import jittor
from jittor.misc import normalize
from typing import Any, Optional, TypeVar
from jittor.nn import Module

# jittor SpectralNorm implementation https://discuss.jittor.org/t/topic/194/3
class SpectralNorm:
    # Invariant before and after each forward call:
    #   u = normalize(W @ v)
    # NB: At initialization, this invariant is not enforced

    _version: int = 1
    # At version 1:
    #   made  `W` not a buffer,
    #   added `v` as a buffer, and
    #   made eval mode use `W = u @ W_orig @ v` rather than the stored `W`.
    name: str
    dim: int
    n_power_iterations: int
    eps: float

    def __init__(self, name: str = 'weight', n_power_iterations: int = 1, dim: int = 0, eps: float = 1e-12) -> None:
        self.name = name
        self.dim = dim
        if n_power_iterations <= 0:
            raise ValueError('Expected n_power_iterations to be positive, but '
                             'got n_power_iterations={}'.format(n_power_iterations))
        self.n_power_iterations = n_power_iterations
        self.eps = eps

    def reshape_weight_to_matrix(self, weight: jittor.Var) -> jittor.Var:
        weight_mat = weight
        if self.dim != 0:
            # permute dim to front
            weight_mat = weight_mat.permute(self.dim,
                                            *[d for d in range(weight_mat.dim()) if d != self.dim])
        height = weight_mat.size(0)
        return weight_mat.reshape(height, -1)

    def compute_weight(self, module: Module, do_power_iteration: bool) -> jittor.Var:
        # NB: If `do_power_iteration` is set, the `u` and `v` vectors are
        #     updated in power iteration **in-place**. This is very important
        #     because in `DataParallel` forward, the vectors (being buffers) are
        #     broadcast from the parallelized module to each module replica,
        #     which is a new module object created on the fly. And each replica
        #     runs its own spectral norm power iteration. So simply assigning
        #     the updated vectors to the module this function runs on will cause
        #     the update to be lost forever. And the next time the parallelized
        #     module is replicated, the same randomly initialized vectors are
        #     broadcast and used!
        #
        #     Therefore, to make the change propagate back, we rely on two
        #     important behaviors (also enforced via tests):
        #       1. `DataParallel` doesn't clone storage if the broadcast tensor
        #          is already on correct device; and it makes sure that the
        #          parallelized module is already on `device[0]`.
        #       2. If the out tensor in `out=` kwarg has correct shape, it will
        #          just fill in the values.
        #     Therefore, since the same power iteration is performed on all
        #     devices, simply updating the tensors in-place will make sure that
        #     the module replica on `device[0]` will update the _u vector on the
        #     parallized module (by shared storage).
        #
        #    However, after we update `u` and `v` in-place, we need to **clone**
        #    them before using them to normalize the weight. This is to support
        #    backproping through two forward passes, e.g., the common pattern in
        #    GAN training: loss = D(real) - D(fake). Otherwise, engine will
        #    complain that variables needed to do backward for the first forward
        #    (i.e., the `u` and `v` vectors) are changed in the second forward.
        weight = getattr(module, self.name + '_orig')
        u = getattr(module, self.name + '_u')
        v = getattr(module, self.name + '_v')
        weight_mat = self.reshape_weight_to_matrix(weight)

        if do_power_iteration:
            with jittor.no_grad():
                for _ in range(self.n_power_iterations):
                    # Spectral norm of weight equals to `u^T W v`, where `u` and `v`
                    # are the first left and right singular vectors.
                    # This power iteration produces approximations of `u` and `v`.
                    v = normalize(jittor.nn.matmul(weight_mat.t(), u), dim=0, eps=self.eps)
                    u = normalize(jittor.nn.matmul(weight_mat, v), dim=0, eps=self.eps)
                if self.n_power_iterations > 0:
                    # See above on why we need to clone
                    u = u.clone()
                    v = v.clone()

        sigma = jittor.matmul(u, jittor.matmul(weight_mat, v))
        weight = weight / sigma
        return weight

    def remove(self, module: Module) -> None:
        with jittor.no_grad():
            weight = self.compute_weight(module, do_power_iteration=False)
        delattr(module, self.name)
        delattr(module, self.name + '_u')
        delattr(module, self.name + '_v')
        delattr(module, self.name + '_orig')
        # module.register_parameter(self.name, jittor.Var(weight.detach()))
        setattr(module, self.name, jittor.Var(weight.detach()))

    def __call__(self, module: Module, inputs: Any) -> None:
        self.compute_weight(module, do_power_iteration=module.is_training())
        # setattr(module, self.name, self.compute_weight(module, do_power_iteration=module.training))

    def _solve_v_and_rescale(self, weight_mat, u, target_sigma):
        # Tries to returns a vector `v` s.t. `u = normalize(W @ v)`
        # (the invariant at top of this class) and `u @ W @ v = sigma`.
        # This uses pinverse in case W^T W is not invertible.
        # v = torch.linalg.multi_dot([weight_mat.t().mm(weight_mat).pinverse(), weight_mat.t(), u.unsqueeze(1)]).squeeze(1)
        v = jittor.matmul(jittor.matmul(weight_mat.t().mm(weight_mat).pinverse(), jittor.matmul(weight_mat.t(), u.unsqueeze(1)))).squeeze(1)
        return v.mul_(target_sigma / jittor.matmul(u, jittor.matmul(weight_mat, v)))

    @staticmethod
    def apply(module: Module, name: str, n_power_iterations: int, dim: int, eps: float) -> 'SpectralNorm':
        # for k, hook in module._forward_pre_hooks.items():
        #     if isinstance(hook, SpectralNorm) and hook.name == name:
        #         raise RuntimeError("Cannot register two spectral_norm hooks on "
        #                            "the same parameter {}".format(name))

        fn = SpectralNorm(name, n_power_iterations, dim, eps)
        weight = module._parameters[name]
        if weight is None:
            raise ValueError(f'`SpectralNorm` cannot be applied as parameter `{name}` is None')
        # if isinstance(weight, torch.nn.parameter.UninitializedParameter):
        #     raise ValueError(
        #         'The module passed to `SpectralNorm` can\'t have uninitialized parameters. '
        #         'Make sure to run the dummy forward before applying spectral normalization')

        with jittor.no_grad():
            weight_mat = fn.reshape_weight_to_matrix(weight)

            h, w = weight_mat.size()
            # randomly initialize `u` and `v`
            # u = normalize(weight.new_empty(h).normal_(0, 1), dim=0, eps=fn.eps)
            # v = normalize(weight.new_empty(w).normal_(0, 1), dim=0, eps=fn.eps)
            u = normalize(jittor.randn([h]), dim=0, eps=fn.eps)
            v = normalize(jittor.randn([w]), dim=0, eps=fn.eps)

        delattr(module, fn.name)
        # module.register_parameter(fn.name + "_orig", weight)
        setattr(module, fn.name + "_orig", weight)
        # We still need to assign weight back as fn.name because all sorts of
        # things may assume that it exists, e.g., when initializing weights.
        # However, we can't directly assign as it could be an nn.Parameter and
        # gets added as a parameter. Instead, we register weight.data as a plain
        # attribute.
        # setattr(module, fn.name, weight.data)
        setattr(module, fn.name, weight)
        # module.register_buffer(fn.name + "_u", u)
        # module.register_buffer(fn.name + "_v", v)
        setattr(module, fn.name + "_u", u)
        setattr(module, fn.name + "_v", v)

        # module.register_forward_pre_hook(fn)
        module.register_pre_forward_hook(fn)
        # module._register_state_dict_hook(SpectralNormStateDictHook(fn))
        # module._register_load_state_dict_pre_hook(SpectralNormLoadStateDictPreHook(fn))
        return fn


# This is a top level class because Py2 pickle doesn't like inner class nor an
# instancemethod.
# class SpectralNormLoadStateDictPreHook:
#     # See docstring of SpectralNorm._version on the changes to spectral_norm.
#     def __init__(self, fn) -> None:
#         self.fn = fn

#     # For state_dict with version None, (assuming that it has gone through at
#     # least one training forward), we have
#     #
#     #    u = normalize(W_orig @ v)
#     #    W = W_orig / sigma, where sigma = u @ W_orig @ v
#     #
#     # To compute `v`, we solve `W_orig @ x = u`, and let
#     #    v = x / (u @ W_orig @ x) * (W / W_orig).
#     def __call__(self, state_dict, prefix, local_metadata, strict,
#                  missing_keys, unexpected_keys, error_msgs) -> None:
#         fn = self.fn
#         version = local_metadata.get('spectral_norm', {}).get(fn.name + '.version', None)
#         if version is None or version < 1:
#             weight_key = prefix + fn.name
#             if version is None and all(weight_key + s in state_dict for s in ('_orig', '_u', '_v')) and \
#                     weight_key not in state_dict:
#                 # Detect if it is the updated state dict and just missing metadata.
#                 # This could happen if the users are crafting a state dict themselves,
#                 # so we just pretend that this is the newest.
#                 return
#             has_missing_keys = False
#             for suffix in ('_orig', '', '_u'):
#                 key = weight_key + suffix
#                 if key not in state_dict:
#                     has_missing_keys = True
#                     if strict:
#                         missing_keys.append(key)
#             if has_missing_keys:
#                 return
#             with jittor.no_grad():
#                 weight_orig = state_dict[weight_key + '_orig']
#                 weight = state_dict.pop(weight_key)
#                 sigma = (weight_orig / weight).mean()
#                 weight_mat = fn.reshape_weight_to_matrix(weight_orig)
#                 u = state_dict[weight_key + '_u']
#                 v = fn._solve_v_and_rescale(weight_mat, u, sigma)
#                 state_dict[weight_key + '_v'] = v


# This is a top level class because Py2 pickle doesn't like inner class nor an
# instancemethod.
# class SpectralNormStateDictHook:
#     # See docstring of SpectralNorm._version on the changes to spectral_norm.
#     def __init__(self, fn) -> None:
#         self.fn = fn

#     def __call__(self, module, state_dict, prefix, local_metadata) -> None:
#         if 'spectral_norm' not in local_metadata:
#             local_metadata['spectral_norm'] = {}
#         key = self.fn.name + '.version'
#         if key in local_metadata['spectral_norm']:
#             raise RuntimeError("Unexpected key in metadata['spectral_norm']: {}".format(key))
#         local_metadata['spectral_norm'][key] = self.fn._version


T_module = TypeVar('T_module', bound=Module)

def spectral_norm(module: T_module,
                  name: str = 'weight',
                  n_power_iterations: int = 1,
                  eps: float = 1e-12,
                  dim: Optional[int] = None) -> T_module:
    r"""Applies spectral normalization to a parameter in the given module.

    .. math::
        \mathbf{W}_{SN} = \dfrac{\mathbf{W}}{\sigma(\mathbf{W})},
        \sigma(\mathbf{W}) = \max_{\mathbf{h}: \mathbf{h} \ne 0} \dfrac{\|\mathbf{W} \mathbf{h}\|_2}{\|\mathbf{h}\|_2}

    Spectral normalization stabilizes the training of discriminators (critics)
    in Generative Adversarial Networks (GANs) by rescaling the weight tensor
    with spectral norm :math:`\sigma` of the weight matrix calculated using
    power iteration method. If the dimension of the weight tensor is greater
    than 2, it is reshaped to 2D in power iteration method to get spectral
    norm. This is implemented via a hook that calculates spectral norm and
    rescales weight before every :meth:`~Module.forward` call.

    See `Spectral Normalization for Generative Adversarial Networks`_ .

    .. _`Spectral Normalization for Generative Adversarial Networks`: https://arxiv.org/abs/1802.05957

    Args:
        module (nn.Module): containing module
        name (str, optional): name of weight parameter
        n_power_iterations (int, optional): number of power iterations to
            calculate spectral norm
        eps (float, optional): epsilon for numerical stability in
            calculating norms
        dim (int, optional): dimension corresponding to number of outputs,
            the default is ``0``, except for modules that are instances of
            ConvTranspose{1,2,3}d, when it is ``1``

    Returns:
        The original module with the spectral norm hook

    .. note::
        This function has been reimplemented as
        :func:`torch.nn.utils.parametrizations.spectral_norm` using the new
        parametrization functionality in
        :func:`torch.nn.utils.parametrize.register_parametrization`. Please use
        the newer version. This function will be deprecated in a future version
        of PyTorch.

    Example::

        >>> m = spectral_norm(nn.Linear(20, 40))
        >>> m
        Linear(in_features=20, out_features=40, bias=True)
        >>> m.weight_u.size()
        torch.Size([40])

    """
    if dim is None:
        if isinstance(module, (jittor.nn.ConvTranspose,
                               jittor.nn.ConvTranspose3d)):
            dim = 1
        else:
            dim = 0
    SpectralNorm.apply(module, name, n_power_iterations, dim, eps)
    return module



# Returns a function that creates a normalization function
# that does not condition on semantic map
def get_nonspade_norm_layer(opt, norm_type='instance'):
    # helper function to get # output channels of the previous layer
    def get_out_channel(layer):
        if hasattr(layer, 'out_channels'):
            return getattr(layer, 'out_channels')
        return layer.weight.size(0)

    # this function will be returned
    def add_norm_layer(layer):
        nonlocal norm_type
        if norm_type.startswith('spectral'):
            layer = spectral_norm(layer)
            subnorm_type = norm_type[len('spectral'):]

        if subnorm_type == 'none' or len(subnorm_type) == 0:
            return layer

        # remove bias in the previous layer, which is meaningless
        # since it has no effect after normalization
        if getattr(layer, 'bias', None) is not None:
            delattr(layer, 'bias')
            layer.bias = None

        if subnorm_type == 'batch':
            norm_layer = nn.BatchNorm2d(get_out_channel(layer), affine=True)
        elif subnorm_type == 'sync_batch':
            norm_layer = BatchNorm(get_out_channel(layer), affine=True, sync=True)
        elif subnorm_type == 'instance':
            norm_layer = nn.InstanceNorm2d(get_out_channel(layer), affine=False)
        else:
            raise ValueError('normalization layer %s is not recognized' % subnorm_type)

        return nn.Sequential(layer, norm_layer)

    return add_norm_layer


def get_2d_sincos_pos_embed_from_grid(embed_dim, grid):
    assert embed_dim % 2 == 0

    # use half of dimensions to encode grid_h
    emb_h = get_1d_sincos_pos_embed_from_grid(embed_dim // 2, grid[0])  # (H*W, D/2)
    emb_w = get_1d_sincos_pos_embed_from_grid(embed_dim // 2, grid[1])  # (H*W, D/2)

    emb = np.concatenate([emb_h, emb_w], axis=1) # (H*W, D)
    return emb


def get_1d_sincos_pos_embed_from_grid(embed_dim, pos):
    """
    embed_dim: output dimension for each position
    pos: a list of positions to be encoded: size (M,)
    out: (M, D)
    """
    assert embed_dim % 2 == 0
    omega = np.arange(embed_dim // 2, dtype=np.float)
    omega /= embed_dim / 2.
    omega = 1. / 10000**omega  # (D/2,)

    pos = pos.reshape(-1)  # (M,)
    out = np.einsum('m,d->md', pos, omega)  # (M, D/2), outer product

    emb_sin = np.sin(out) # (M, D/2)
    emb_cos = np.cos(out) # (M, D/2)

    emb = np.concatenate([emb_sin, emb_cos], axis=1)  # (M, D)
    return emb


def get_2d_sincos_pos_embed(embed_dim, grid_h_sz, grid_w_sz):
    grid_h = np.arange(grid_h_sz, dtype=np.float16)
    grid_w = np.arange(grid_w_sz, dtype=np.float16)
    grid = np.meshgrid(grid_w, grid_h)
    grid = np.stack(grid, axis=0)

    grid = grid.reshape([2, 1, grid_h_sz, grid_w_sz])
    pos_embed = get_2d_sincos_pos_embed_from_grid(embed_dim, grid)
    return pos_embed

# Creates SPADE normalization layer based on the given configuration
# SPADE consists of two steps. First, it normalizes the activations using
# your favorite normalization method, such as Batch Norm or Instance Norm.
# Second, it applies scale and bias to the normalized output, conditioned on
# the segmentation map.
# The format of |config_text| is spade(norm)(ks), where
# (norm) specifies the type of parameter-free normalization.
#       (e.g. syncbatch, batch, instance)
# (ks) specifies the size of kernel in the SPADE module (e.g. 3x3)
# Example |config_text| will be spadesyncbatch3x3, or spadeinstance5x5.
# Also, the other arguments are
# |norm_nc|: the #channels of the normalized activations, hence the output dim of SPADE
# |label_nc|: the #channels of the input semantic map, hence the input dim of SPADE
class SPADE(nn.Module):
    def __init__(self, config_text, norm_nc, label_nc, use_pos=False, use_pos_proj=False, add_noise = False, opt=None):
        super().__init__()

        assert config_text.startswith('spade')
        parsed = re.search('spade(\D+)(\d)x\d', config_text)
        param_free_norm_type = str(parsed.group(1))
        ks = int(parsed.group(2))

        if param_free_norm_type == 'instance':
            self.param_free_norm = nn.InstanceNorm2d(norm_nc, affine=False)
        elif param_free_norm_type == 'syncbatch':
            self.param_free_norm = BatchNorm(norm_nc, affine=False, sync=True)
        elif param_free_norm_type == 'batch':
            self.param_free_norm = nn.BatchNorm2d(norm_nc, affine=False)
        else:
            raise ValueError('%s is not a recognized param-free norm type in SPADE'
                             % param_free_norm_type)
        self.add_noise = add_noise
        self.use_pos = use_pos
        self.use_pos_proj = use_pos_proj
        if opt.use_seg_noise:
            k = opt.use_seg_noise_kernel
            self.seg_noise_var = nn.Conv2d(label_nc, norm_nc, k, padding=(k-1)//2)
            init.constant_(self.seg_noise_var.weight, 0.0)
            init.constant_(self.seg_noise_var.bias, 0.0)

        # The dimension of the intermediate embedding space. Yes, hardcoded.
        nhidden = 128
        if self.add_noise:
            self.noise_var = nn.Parameter(jt.zeros(norm_nc), requires_grad=True)
        self.pos_embed = None
        if use_pos_proj:
            self.pos_proj = nn.Conv2d(nhidden, nhidden, kernel_size=1)

        pw = ks // 2
        self.mlp_shared = nn.Sequential(
            nn.Conv2d(label_nc, nhidden, kernel_size=ks, padding=pw),
            nn.ReLU()
        )
        self.mlp_gamma = nn.Conv2d(nhidden, norm_nc, kernel_size=ks, padding=pw)
        self.mlp_beta = nn.Conv2d(nhidden, norm_nc, kernel_size=ks, padding=pw)
        self.opt = opt

    def execute(self, x, segmap):

        # Part 1. generate parameter-free normalized activations
        if self.opt.use_seg_noise:
            seg = nn.interpolate(segmap, size=x.size()[2:], mode='nearest')
            noise = self.seg_noise_var(seg)
            added_noise = (jt.randn(noise.shape[0], 1, noise.shape[2], noise.shape[3]) * noise)
            normalized = self.param_free_norm(x + added_noise)

        elif self.add_noise:
            added_noise = (jt.randn(x.shape[0], x.shape[3], x.shape[2], 1) * self.noise_var).transpose(1, 3)
            normalized = self.param_free_norm(x + added_noise)
        else: 
            normalized = self.param_free_norm(x)
        # Part 2. produce scaling and bias conditioned on semantic map
        segmap = nn.interpolate(segmap, size=x.size()[2:], mode='nearest')
        actv = self.mlp_shared(segmap)
        if self.use_pos: # default with True
            if self.pos_embed is None:
                B, C, H, W = actv.size()
                pos_embed = jt.from_numpy(get_2d_sincos_pos_embed(C, H, W)).to(actv.device)
                self.pos_embed = pos_embed.permute(0, 1).reshape(1, C, H, W)
            if self.use_pos_proj: # default with True
                actv += self.pos_proj(self.pos_embed.to(jt.float16))
            else:
                actv += self.pos_embed

        gamma = self.mlp_gamma(actv)
        beta = self.mlp_beta(actv)

        # apply scale and bias
        out = normalized * (1 + gamma) + beta

        return out