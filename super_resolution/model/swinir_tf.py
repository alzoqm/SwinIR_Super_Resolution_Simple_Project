import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
import torch
import os
import cv2
import tqdm
import torch
import math
import h5py
import imageio
import random

from tqdm import tqdm

class MLP(tf.keras.Model):
  def __init__(self, emb_size, dff, dropout):
    super().__init__()
    self.linear1 = tf.keras.layers.Dense(dff)
    self.linear2 = tf.keras.layers.Dense(emb_size)
    self.dropout = tf.keras.layers.Dropout(dropout)

  def call(self, x):
    x = self.linear1(x)
    x = tf.nn.gelu(x)
    x = self.dropout(x)
    x = self.linear2(x)
    x = self.dropout(x)
    return x

def window_partition(x, window_size):
  _, H, W, C = x.shape
  x = tf.reshape(x, shape=(-1, H // window_size, window_size, W // window_size, window_size, C))
  windows = tf.transpose(x, perm=(0, 1, 3, 2, 4, 5))
  windows = tf.reshape(windows, shape=(-1, window_size, window_size, C))
  return windows

def window_reverse(windows, window_size, H, W, emb_size):
  #B = int(windows.shape[0] / (H * W / window_size / window_size))
  x = tf.reshape(windows, shape=(-1, H // window_size, W // window_size, window_size, window_size, emb_size))
  x = tf.transpose(x, perm=(0, 1, 3, 2, 4, 5))
  x = tf.reshape(x, shape=(-1, H, W, emb_size))
  return x

class DropPath(tf.keras.layers.Layer):
  def __init__(self, drop_prob, **kwargs):
    super().__init__(**kwargs)
    self.drop_prob = drop_prob

  def call(self, x):
    input_shape = tf.shape(x)
    batch_size = input_shape[0]
    rank = x.shape.rank
    shape = (batch_size,) + (1,) * (rank - 1)
    random_tensor = (1 - self.drop_prob) + tf.random.uniform(shape, dtype=x.dtype)
    path_mask = tf.floor(random_tensor)
    output = tf.math.divide(x, 1 - self.drop_prob) * path_mask
    return output

class WindowAttention(tf.keras.Model):
  def __init__(self, emb_size, window_size, num_heads, qkv_bias=True, attn_drop=0., proj_drop=0.):
    super().__init__()
    self.emb_size = emb_size
    self.window_size = window_size
    self.num_heads = num_heads
    head_dim = emb_size // num_heads
    self.scale = head_dim ** -0.5

    num_window_elements = (2 * self.window_size[0] - 1) * (2 * self.window_size[1] - 1)
    self.relative_position_bias_table = self.add_weight(shape=(num_window_elements, self.num_heads), initializer=tf.initializers.Zeros(), trainable=True, name=self.name + '/relative_position_bias_table')

    coords_h = np.arange(self.window_size[0])
    coords_w = np.arange(self.window_size[1])
    coords_matrix = np.meshgrid(coords_h, coords_w, indexing="ij")
    coords = np.stack(coords_matrix)
    coords_flatten = coords.reshape(2, -1)

    relative_coords = coords_flatten[:, :, None] - coords_flatten[:, None, :]
    relative_coords = relative_coords.transpose([1, 2, 0])
    relative_coords[:, :, 0] += self.window_size[0] - 1
    relative_coords[:, :, 1] += self.window_size[1] - 1
    relative_coords[:, :, 0] *= 2 * self.window_size[1] - 1
    relative_position_index = relative_coords.sum(-1)

    self.relative_position_index = tf.Variable(initial_value=tf.convert_to_tensor(relative_position_index), trainable=False, name=self.name + '/relative_position_index')

    self.q_linear = tf.keras.layers.Dense(emb_size, use_bias=qkv_bias)
    self.k_linear = tf.keras.layers.Dense(emb_size, use_bias=qkv_bias)
    self.v_linear = tf.keras.layers.Dense(emb_size, use_bias=qkv_bias)
    self.proj = tf.keras.layers.Dense(emb_size)

    self.proj_drop = tf.keras.layers.Dropout(proj_drop)
    self.attn_drop = tf.keras.layers.Dropout(attn_drop)


  def call(self, q, k, v, mask=None):
    B_, N, C = q.shape
    q = self.q_linear(q)
    k = self.k_linear(k)
    v = self.v_linear(v)

    q = tf.reshape(q, shape=(-1, N, self.num_heads, C // self.num_heads))
    q = tf.transpose(q, perm=(0, 2, 1, 3))

    k = tf.reshape(k, shape=(-1, N, self.num_heads, C // self.num_heads))
    k = tf.transpose(k, perm=(0, 2, 1, 3))

    v = tf.reshape(v, shape=(-1, N, self.num_heads, C // self.num_heads))
    v = tf.transpose(v, perm=(0, 2, 1, 3))
    q = q*self.scale
    attn = tf.matmul(q, k, transpose_b=True)

    num_window_elements = self.window_size[0] * self.window_size[1]
    relative_position_index_flat = tf.reshape(self.relative_position_index, shape=(-1, ))

    relative_position_bias = tf.gather(self.relative_position_bias_table, relative_position_index_flat)
    relative_position_bias = tf.reshape(relative_position_bias, shape=(num_window_elements, num_window_elements, -1))
    relative_position_bias = tf.transpose(relative_position_bias, perm=(2, 0, 1))
    attn = attn + tf.expand_dims(relative_position_bias, axis=0)

    if mask is not None:
      nW = mask.shape[0]
      #attn = tf.reshape(attn, shape=(B_ // nW, nW, self.num_heads, N, N))
      attn = tf.reshape(attn, shape=(-1, nW, self.num_heads, N, N))
      mask_expand = tf.expand_dims(mask, axis=1)
      mask_expand = tf.expand_dims(mask_expand, axis=0)
      mask_expand = tf.cast(mask_expand, dtype=tf.float32)
      attn = attn + mask_expand
      attn = tf.reshape(attn, shape=(-1, self.num_heads, N, N))
      attn = tf.keras.activations.softmax(attn, axis=-1)
    else:
      attn = tf.keras.activations.softmax(attn, axis=-1)

    attn = self.attn_drop(attn)

    x = (attn @ v)
    x = tf.transpose(x, perm=(0, 2, 1, 3))
    x = tf.reshape(x, shape=(-1, N, C))
    x = self.proj(x)
    x = self.proj_drop(x)

    return x, attn


class SwinTransformerBlock(tf.keras.Model):
  def __init__(self, emb_size, input_resolution, num_heads, window_size=7, shift_size=0, mlp_ratio=4., qkv_bias=True, dropout=0., attn_drop=0., drop_path=0.1):
    super().__init__()
    self.emb_size = emb_size
    self.input_resolution = input_resolution
    self.num_heads = num_heads
    self.window_size = window_size
    self.shift_size = shift_size
    self.mlp_ratio = mlp_ratio
    if min(self.input_resolution) <= self.window_size:
      self.shift_size = 0
      self.window_size = min(self.input_resolution)

    assert 0 <= self.shift_size < self.window_size, "shift_size must in 0-window_size"

    self.norm1 = tf.keras.layers.LayerNormalization(epsilon=1e-7)
    self.attn = WindowAttention(emb_size, (window_size, window_size), num_heads, qkv_bias, attn_drop, proj_drop=dropout)

    self.drop_path = DropPath(drop_path)
    self.norm2 = tf.keras.layers.LayerNormalization(epsilon=1e-7)
    dff = emb_size * mlp_ratio
    self.mlp = MLP(emb_size, dff, dropout)

    if self.shift_size > 0:
      attn_mask = self.calculate_mask(self.input_resolution)
    else:
      attn_mask = None
    if attn_mask is not None:
      self.attn_mask = tf.Variable(initial_value=attn_mask, trainable=False, name=self.name + '/attn_mask')
    else:
      self.attn_mask = None

  def calculate_mask(self, x_size):
    H, W = x_size
    img_mask = np.zeros(shape=(1, H, W, 1))
    h_slices = (slice(0, -self.window_size),
                slice(-self.window_size, -self.shift_size),
                slice(-self.shift_size, None))
    w_slices = (slice(0, -self.window_size),
                slice(-self.window_size, -self.shift_size),
                slice(-self.shift_size, None))
    cnt = 0
    for h in h_slices:
      for w in w_slices:
        img_mask[:, h, w, :] = cnt
        cnt += 1

    mask_windows = window_partition(img_mask, self.window_size)
    mask_windows = tf.reshape(mask_windows, shape=(-1, self.window_size * self.window_size))
    attn_mask = tf.expand_dims(mask_windows, axis=1) - tf.expand_dims(mask_windows, axis=2)
    attn_mask = tf.cast(attn_mask, dtype=tf.float32)
    attn_mask = tf.where(attn_mask != 0 , -100.0, attn_mask)
    attn_mask = tf.where(attn_mask == 0, 0.0, attn_mask)
    return attn_mask

  def call(self, x, x_size):
    H, W = x_size
    B, L, C = x.shape

    shortcut = x

    x = tf.reshape(x, shape=(-1, H, W, C))

    if self.shift_size > 0:
      shifted_x = tf.roll(x, shift=[-self.shift_size, -self.shift_size], axis=[1, 2])
    else:
      shifted_x = x

    x_windows = window_partition(shifted_x, self.window_size)
    x_windows = tf.reshape(x_windows, shape=(-1, self.window_size * self.window_size, C))

    if self.input_resolution == x_size:
      attn_windows, attn = self.attn(x_windows, x_windows, x_windows, mask=self.attn_mask)
    else:
      attn_windows, attn = self.attn(x_windows, x_windows, x_windows, mask=self.calculate_mask(x_size))
    
    attn_windows = tf.reshape(attn_windows, shape=(-1, self.window_size, self.window_size, C))
    shifted_x = window_reverse(attn_windows, self.window_size, H, W, self.emb_size)
    
    if self.shift_size > 0:
      x = tf.roll(shifted_x, shift=[self.shift_size, self.shift_size], axis=[1, 2])
    else:
      x = shifted_x
    
    x = tf.reshape(x, shape=(-1, H*W, C))
    x = self.norm1(shortcut + self.drop_path(x))
    x = self.norm2(x + self.drop_path(self.mlp(x)))

    return x

class BasicLayer(tf.keras.Model):
  def __init__(self, emb_size, input_resolution, depth, num_heads, window_size, mlp_ratio=4., qkv_bias=True, dropout=0., attn_drop=0., drop_path=0.1):
    super().__init__()
    self.emb_size = emb_size
    self.input_resolution = input_resolution
    self.depth = depth

    self.blocks = [SwinTransformerBlock(emb_size=emb_size, input_resolution=input_resolution, num_heads=num_heads, window_size=window_size, shift_size=0 if (i%2 == 0) else window_size // 2,
                                        mlp_ratio=mlp_ratio, qkv_bias=qkv_bias, dropout=dropout, attn_drop=attn_drop, drop_path=drop_path) for i in range(depth)]
    
  def call(self, x, x_size):
    for blk in self.blocks:
      x = blk(x, x_size)

    return x

class PatchEmbed(tf.keras.Model):
  def __init__(self, img_size=224, patch_size=4, in_chans=3, emb_size=96, layer_norm=False):
    super().__init__()
    if type(img_size) == int:
      img_size = (img_size, img_size)

    if type(patch_size) == int:
      patch_size = (patch_size, patch_size)

    patches_resolution = [img_size[0] // patch_size[0], img_size[1] // patch_size[1]]
    self.img_size = img_size
    self.patch_size = patch_size
    self.patches_resolution = patches_resolution
    self.num_patches = patches_resolution[0] * patches_resolution[1]

    self.in_chans = in_chans
    self.emb_size = emb_size

    if layer_norm == True:
      self.norm = tf.keras.layers.LayerNormalization(epsilon=1e-7)
    else:
      self.norm = None

  def call(self, x):
    _, H, W, C = x.shape
    x = tf.reshape(x, shape=(-1, H*W, C))
    if self.norm is not None:
      x = self.norm(x)

    return x


class PatchUnEmbed(tf.keras.Model):
  def __init__(self, img_size=224, patch_size=4, in_chans=3, emb_size=96, layer_norm=False):
    super().__init__()
    if type(img_size) == int:
      img_size = (img_size, img_size)
    
    if type(patch_size) == int:
      patch_size = (patch_size, patch_size)
    
    patches_resolution = [img_size[0] // patch_size[0], img_size[1] // patch_size[1]]
    self.img_size = img_size
    self.patch_size = patch_size
    self.patches_resolution = patches_resolution
    self.num_patches = patches_resolution[0] * patches_resolution[1]

    self.in_chans = in_chans
    self.emb_size = emb_size

  def call(self, x, x_size):
    B, HW, C = x.shape
    x = tf.reshape(x, shape=(-1, x_size[0], x_size[1], self.emb_size))
    return x

class RSTB(tf.keras.Model):
  def __init__(self, emb_size, input_resolution, depth, num_heads, window_size, mlp_ratio=4., qkv_bias=True, dropout=0.1, attn_drop=0., drop_path=0.1, img_size=224, patch_size=4, resi_connection='3conv'):
    super().__init__()
    self.emb_size = emb_size
    self.input_resolution = input_resolution

    self.residual_group = BasicLayer(emb_size, input_resolution, depth, num_heads, window_size, mlp_ratio, qkv_bias, dropout, attn_drop, drop_path)
    if resi_connection == '1conv':
      self.conv = tf.keras.layers.Conv2D(emb_size, 3, padding='same')
    else:
      self.conv = tf.keras.Sequential([
                                       tf.keras.layers.Conv2D(emb_size // 4, 3, padding='same', activation=tf.nn.leaky_relu),
                                       tf.keras.layers.Conv2D(emb_size // 4, 3, padding='same', activation=tf.nn.leaky_relu),
                                       tf.keras.layers.Conv2D(emb_size, 3, padding='same')
      ])
    
    self.patch_embed = PatchEmbed(img_size, patch_size, in_chans=0, emb_size=emb_size, layer_norm=False)
    self.patch_unembed = PatchUnEmbed(img_size, patch_size, in_chans=0, emb_size=emb_size, layer_norm=False)

  def call(self, x, x_size):
    return self.patch_embed(self.conv(self.patch_unembed(self.residual_group(x, x_size), x_size))) + x

class Upsample(tf.keras.Model):
  def __init__(self, scale, num_feat):
    super().__init__()
    self.m = []
    self.scale = scale
    self.num_feat = num_feat
    self.scale_check = False
    if (scale & (scale-1) == 0):
      self.scale_check = True
      for _ in range(int(math.log(scale, 2))):
        self.m.append(tf.keras.layers.Conv2D(4 * num_feat, 3, 1, padding='same'))
    elif scale == 3:
      self.scale_check = False
      self.m.append(tf.keras.layers.Conv2D(9 * num_feat, 3, 1, padding='same'))
    else:
      raise ValueError(f"scale {scale} is not supported. scale: 2^n and 3.")
  
  def call(self, x):
    for con in self.m:
      x = con(x)
      if self.scale_check == True:
        x = tf.nn.depth_to_space(x, 2)
      elif self.scale_check == False:
        x = tf.nn.depth_to_space(x, 3)
      
    return x

class swinIR(tf.keras.Model):
  def __init__(self, img_size=64, patch_size=1, in_chans=3, emb_size=180, depths=[6, 6, 6, 6], num_heads=[6, 6, 6, 6],
               window_size=7, mlp_ratio=4., qkv_bias=True, drop_rate=0., attn_drop_rate=0., drop_path_rate=0.1, ape=False, patch_norm=True,
               upscale=2, img_range=1., resi_connection='3conv', **kwargs):
    super().__init__()
    num_in_ch = in_chans
    num_out_ch = in_chans
    num_feat = 64
    self.img_range = img_range
    if in_chans == 3:
      rgb_mean = (0.4488, 0.4371, 0.4040)
      self.mean = tf.convert_to_tensor(rgb_mean)
      self.mean = tf.reshape(self.mean, shape=(1, 1, 1, 3))
    else:
      self.mean = tf.zeros(shape=(1, 1, 1, 1))
    self.window_size = window_size

    self.conv_first = tf.keras.layers.Conv2D(emb_size, 3, padding='same')

    self.num_layers = len(depths)
    self.emb_size = emb_size
    self.ape = ape
    self.patch_norm = patch_norm
    self.num_features = emb_size
    self.mlp_ration = mlp_ratio
    self.upscale = upscale

    self.patch_embed = PatchEmbed(img_size=img_size, patch_size=patch_size, in_chans=emb_size, emb_size=emb_size, layer_norm=patch_norm)
    num_patches = self.patch_embed.num_patches
    patches_resolution = self.patch_embed.patches_resolution
    self.patches_resolution = patches_resolution

    self.patch_unembed = PatchUnEmbed(img_size=img_size, patch_size=patch_size, in_chans=emb_size, emb_size=emb_size, layer_norm=patch_norm)

    if self.ape:
      self.absolute_pos_emb = tf.Variable(tf.zeros(shape=(1, num_patches, emb_size), name=self.name + '/self.absolute_pos_emb'))
    
    self.pos_drop = tf.keras.layers.Dropout(drop_rate)

    self.rstb_layers = [RSTB(emb_size=emb_size, input_resolution=(patches_resolution[0], patches_resolution[1]), depth=depths[i], num_heads=num_heads[i], window_size=window_size,
                        mlp_ratio=mlp_ratio, qkv_bias=qkv_bias, dropout=drop_rate, attn_drop=attn_drop_rate, drop_path=drop_path_rate, img_size=img_size,
                        patch_size=patch_size, resi_connection=resi_connection) for i in range(self.num_layers)]
    
    self.norm = tf.keras.layers.LayerNormalization(epsilon=1e-7)

    if resi_connection=='1conv':
      self.conv_after_body = tf.keras.layers.Conv2D(emb_size, 3, padding='same')
    else:
      self.conv_after_body = tf.keras.Sequential([
                                                  tf.keras.layers.Conv2D(emb_size//4, 3, padding='same', activation=tf.nn.leaky_relu),
                                                  tf.keras.layers.Conv2D(emb_size//4, 3, padding='same', activation=tf.nn.leaky_relu),
                                                  tf.keras.layers.Conv2D(emb_size, 3, padding='same')
      ])

    if self.upscale > 1.:
      self.conv_before_upsample = tf.keras.layers.Conv2D(num_feat, 3, padding='same', activation=tf.nn.leaky_relu)
      self.upsample = Upsample(upscale, num_feat)
      self.conv_last = tf.keras.layers.Conv2D(num_out_ch, 3, padding='same')
    else:
      self.conv_last = tf.keras.Sequential([
                                            tf.keras.layers.Conv2D(num_feat, 3, padding='same', activation=tf.nn.leaky_relu),
                                            tf.keras.layers.Conv2D(num_out_ch, 3, padding='same')
      ])
    

  def check_img_size(self, x):
    _, h, w, _ = x.shape
    mod_pad_h = (self.window_size - h % self.window_size) % self.window_size
    mod_pad_w = (self.window_size - w % self.window_size) % self.window_size
    pad = [[0, 0], [0, mod_pad_h], [0, mod_pad_w], [0, 0]]
    x = tf.pad(x, paddings=pad, mode="REFLECT")
    return x
  
  def call_features(self, x):
    x_size = (x.shape[1], x.shape[2])
    x = self.patch_embed(x)
    if self.ape:
      x = x + self.absolute_pos_emb

    x = self.pos_drop(x)

    for layer in self.rstb_layers:
      x = layer(x, x_size)
    
    x = self.norm(x)
    x = self.patch_unembed(x, x_size)
    return x

  def call(self, x):
    H, W = x.shape[1], x.shape[2]
    x = self.check_img_size(x)

    self.mean = tf.cast(self.mean, dtype=x.dtype)
    x = (x - self.mean) * self.img_range

    if self.upscale > 1.:
      x = self.conv_first(x)
      x = self.conv_after_body(self.call_features(x)) + x
      x = self.conv_before_upsample(x)
      x = self.conv_last(self.upsample(x))
    else:
      x_first = self.conv_first(x)
      res = self.conv_after_body(self.call_features(x_first)) + x_first
      x = x + self.conv_last(res)

    x = x / self.img_range + self.mean

    return x[:, :H*self.upscale, :W*self.upscale, :]

#####PARAMETERS######
IMG_SIZE = 256
PATCH_SIZE = 1
IN_CHANS = 3
EMB_SIZE = 180
DEPTHS = [6, 6, 6, 6]
NUM_HEADS = [6, 6, 6, 6]
WINDOW_SIZE = 4
MLP_RATIO = 4
QKV_BIAS = True
DROP_RATE = 0.1
ATTN_DROP_RATE = 0.1
DROP_PATH_RATE = 0.1
APE = False
PATCH_NORM = True
UPSCALE = 4
IMG_RANGE = 255
RESI_CONNECTION = '3conv'
