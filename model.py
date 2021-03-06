
"""
Builds the model

-- Summary of functions --

Building the graph:
  inference()
  cal_loss()
  train()

"""


""" Importing libraries """

import os # provides a portable way of using operating system dependent functionality, example read file
import re

import tensorflow as tf
from tensorflow.python.framework import ops

import numpy as np
import time
import math
from math import ceil
from tensorflow.python.ops import gen_nn_ops

FLAGS = tf.app.flags.FLAGS

# modules
import Utils
from Inputs import *

#creating own gradient function that does not exist by default
  # not sure why and where it is used? Somewhere in the deconvolution?
@ops.RegisterGradient("MaxPoolWithArgmax")
def _MaxPoolWithArgmaxGrad(op, grad, unused_argmax_grad):
  return gen_nn_ops._max_pool_grad(op.inputs[0],
                                   op.outputs[0],
                                   grad,
                                   op.get_attr("ksize"),
                                   op.get_attr("strides"),
                                   padding=op.get_attr("padding"),
                                   data_format='NHWC')

#tf.control_flow_ops = tf #fix for dropout?

#inference_basic
def inference_basic(images, phase_train, batch_size, keep_prob):
  """ Inference builds the graph as far as is required for running the network forward
      to make predictions.

      The arcitecure has 4(5) different layer sizes, each appear twice
      - once in the encoder and once in the decoder. Each "block" of layers (with the sames size)
      are of different types. For example block one has two conv-batch-relu layers and one pooling layer.

      Args:
        images: Images Tensors (placeholder with correct shape, img_h, img_w, img_d)
        phase_train:

      Returns:
        logit (scores for the classes, that sums up to 1)
  """
  # norm1
    #tf.nn.lrn = local response normalization
  norm1 = tf.nn.lrn(images, depth_radius=5, bias=1.0, alpha=0.0001, beta=0.75,
                         name='norm1')
  # conv1
    #input to: (inputT, shape, train_phase, activation=True, name=None)
    #shape is used to create kernel (kernel is the filter that will be convolved over the input)
    #shape = [patch_size_width, patch_size_heigh, input_channels, output_channels]
  conv1 = conv_layer_with_bn(norm1, [7, 7, images.get_shape().as_list()[3], 64], phase_train, name="conv1")
  # pool1
    #max_pool_with_argmax: Args: input tensor to pool over, ksize=window size for input tensor.
    #strides = [bach_size, image_rows, image_cols, number_of_colors].
    #[1,2,2,1] -> want to apply the filters with stride of two in both dimensions per image (2D).
  pool1, pool1_indices = tf.nn.max_pool_with_argmax(conv1, ksize=[1, 2, 2, 1],
                         strides=[1, 2, 2, 1], padding='SAME', name='pool1')

  conv2 = conv_layer_with_bn(pool1, [7, 7, 64, 64], phase_train, name="conv2")
  pool2, pool2_indices = tf.nn.max_pool_with_argmax(conv2, ksize=[1, 2, 2, 1],
                         strides=[1, 2, 2, 1], padding='SAME', name='pool2')

  conv3 = conv_layer_with_bn(pool2, [7, 7, 64, 64], phase_train, name="conv3")
  pool3, pool3_indices = tf.nn.max_pool_with_argmax(conv3, ksize=[1, 2, 2, 1],
                         strides=[1, 2, 2, 1], padding='SAME', name='pool3')

  conv4 = conv_layer_with_bn(pool3, [7, 7, 64, 64], phase_train, name="conv4")
  pool4, pool4_indices = tf.nn.max_pool_with_argmax(conv4, ksize=[1, 2, 2, 1],
                         strides=[1, 2, 2, 1], padding='SAME', name='pool4')

  """ End of encoder """

  """ Start decoder """

  upsample4 = deconv_layer(pool4, [2, 2, 64, 64], [batch_size, FLAGS.image_h//8, FLAGS.image_w//8, 64], 2, "up4")
  conv_decode4 = conv_layer_with_bn(upsample4, [7, 7, 64, 64], phase_train, False, name="conv_decode4")

  upsample3= deconv_layer(conv_decode4, [2, 2, 64, 64], [batch_size, FLAGS.image_h//4, FLAGS.image_w//4, 64], 2, "up3")
  conv_decode3 = conv_layer_with_bn(upsample3, [7, 7, 64, 64], phase_train, False, name="conv_decode3")

  upsample2= deconv_layer(conv_decode3, [2, 2, 64, 64], [batch_size, FLAGS.image_h//2, FLAGS.image_w//2, 64], 2, "up2")
  conv_decode2 = conv_layer_with_bn(upsample2, [7, 7, 64, 64], phase_train, False, name="conv_decode2")

  upsample1= deconv_layer(conv_decode2, [2, 2, 64, 64], [batch_size, FLAGS.image_h, FLAGS.image_w, 64], 2, "up1")
  conv_decode1 = conv_layer_with_bn(upsample1, [7, 7, 64, 64], phase_train, False, name="conv_decode1")
  """ End of decoder """

  """ Start Classify """
  # output predicted class number (2)
  with tf.variable_scope('conv_classifier') as scope: #all variables prefixed with "conv_classifier/"
    shape=[1, 1, 64, FLAGS.num_class]
    if(FLAGS.conv_init == "msra"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=msra_initializer(1, 64),
                                           wd=0.0005)
    elif(FLAGS.conv_init == "var_scale"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           #initializer=tf.contrib.layers.xavier_initializer(), #orthogonal_initializer()
                                           initializer=tf.contrib.layers.variance_scaling_initializer(), #orthogonal_initializer()
                                           wd=None)
    elif(FLAGS.conv_init == "xavier"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=tf.contrib.layers.xavier_initializer(), #orthogonal_initializer()
                                           wd=None)

    conv = tf.nn.conv2d(conv_decode1, kernel, [1, 1, 1, 1], padding='SAME')
    biases = _variable_on_cpu('biases', [FLAGS.num_class], tf.constant_initializer(0.0))
    conv_classifier = tf.nn.bias_add(conv, biases, name=scope.name) #tf.nn.bias_add is an activation function. Simple add that specifies 1-D tensor bias
    #logit = conv_classifier = prediction
  return conv_classifier

#inference_full_layers_dropout
def inference(images, phase_train, batch_size, keep_prob):
  """ Inference builds the graph as far as is required for running the network forward
      to make predictions.

      The architecure has 5 different layer sizes, each appear twice
      - once in the encoder and once in the decoder. Each "block" of layers (with the sames size)
      are of different types. For example block one has two conv-batch-relu layers and one pooling layer.

      Args:
        images: Images Tensors (placeholder with correct shape, img_h, img_w, img_d)
        phase_train:

      Returns:
        logit (scores for the classes, that sums up to 1)
  """
  conv1_1 = conv_layer_with_bn(images, [7, 7, images.get_shape().as_list()[3], 64], phase_train, name="conv1_1")
  conv1_2 = conv_layer_with_bn(conv1_1, [7, 7, 64, 64], phase_train, name="conv1_2")
  dropout1 = tf.layers.dropout(conv1_2, rate=(1-keep_prob), training=phase_train, name="dropout1")
  pool1, pool1_indices = tf.nn.max_pool_with_argmax(dropout1, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool1')
  conv2_1 = conv_layer_with_bn(pool1, [7, 7, 64, 64], phase_train, name="conv2_1")
  conv2_2 = conv_layer_with_bn(conv2_1, [7, 7, 64, 64], phase_train, name="conv2_2")
  dropout2 = tf.layers.dropout(conv2_2, rate=(1-keep_prob), training=phase_train, name="dropout2")
  pool2, pool2_indices = tf.nn.max_pool_with_argmax(dropout2, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool2')
  conv3_1 = conv_layer_with_bn(pool2, [7, 7, 64, 64], phase_train, name="conv3_1")
  conv3_2 = conv_layer_with_bn(conv3_1, [7, 7, 64, 64], phase_train, name="conv3_2")
  conv3_3 = conv_layer_with_bn(conv3_2, [7, 7, 64, 64], phase_train, name="conv3_3")
  dropout3 = tf.layers.dropout(conv3_3, rate=(1-keep_prob), training=phase_train, name="dropout3")
  pool3, pool3_indices = tf.nn.max_pool_with_argmax(dropout3, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool3')
  conv4_1 = conv_layer_with_bn(pool3, [7, 7, 64, 64], phase_train, name="conv4_1")
  conv4_2 = conv_layer_with_bn(conv4_1, [7, 7, 64, 64], phase_train, name="conv4_2")
  conv4_3 = conv_layer_with_bn(conv4_2, [7, 7, 64, 64], phase_train, name="conv4_3")
  dropout4 = tf.layers.dropout(conv4_3, rate=(1-keep_prob), training=phase_train, name="dropout4")
  pool4, pool4_indices = tf.nn.max_pool_with_argmax(dropout4, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool4')
  conv5_1 = conv_layer_with_bn(pool4, [7, 7, 64, 64], phase_train, name="conv5_1")
  conv5_2 = conv_layer_with_bn(conv5_1, [7, 7, 64, 64], phase_train, name="conv5_2")
  conv5_3 = conv_layer_with_bn(conv5_2, [7, 7, 64, 64], phase_train, name="conv5_3")
  dropout5 = tf.layers.dropout(conv5_3, rate=(1-keep_prob), training=phase_train, name="dropout5")
  pool5, pool5_indices = tf.nn.max_pool_with_argmax(dropout5, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool5')
  """ End of encoder """

  """ Start decoder """
  dropout5_decode = tf.layers.dropout(pool5, rate=(1-keep_prob), training=phase_train, name="dropout5_decode")
  upsample5 = deconv_layer(dropout5_decode, [2, 2, 64, 64], [batch_size, FLAGS.image_h//16, FLAGS.image_w//16, 64], 2, "up5")
  conv_decode5_1 = conv_layer_with_bn(upsample5, [7, 7, 64, 64], phase_train, True, name="conv_decode5_1")
  conv_decode5_2 = conv_layer_with_bn(conv_decode5_1, [7, 7, 64, 64], phase_train, True, name="conv_decode5_2")
  conv_decode5_3 = conv_layer_with_bn(conv_decode5_2, [7, 7, 64, 64], phase_train, True, name="conv_decode5_3")

  dropout4_decode = tf.layers.dropout(conv_decode5_3, rate=(1-keep_prob), training=phase_train, name="dropout4_decode")
  upsample4 = deconv_layer(dropout4_decode, [2, 2, 64, 64], [batch_size, FLAGS.image_h//8, FLAGS.image_w//8, 64], 2, "up4")
  conv_decode4_1 = conv_layer_with_bn(upsample4, [7, 7, 64, 64], phase_train, True, name="conv_decode4_1")
  conv_decode4_2 = conv_layer_with_bn(conv_decode4_1, [7, 7, 64, 64], phase_train, True, name="conv_decode4_2")
  conv_decode4_3 = conv_layer_with_bn(conv_decode4_2, [7, 7, 64, 64], phase_train, True, name="conv_decode4_3")

  dropout3_decode = tf.layers.dropout(conv_decode4_3, rate=(1-keep_prob), training=phase_train, name="dropout3_decode")
  upsample3 = deconv_layer(dropout3_decode, [2, 2, 64, 64], [batch_size, FLAGS.image_h//4, FLAGS.image_w//4, 64], 2, "up3")
  conv_decode3_1 = conv_layer_with_bn(upsample3, [7, 7, 64, 64], phase_train, True, name="conv_decode3_1")
  conv_decode3_2 = conv_layer_with_bn(conv_decode3_1, [7, 7, 64, 64], phase_train, True, name="conv_decode3_2")
  conv_decode3_3 = conv_layer_with_bn(conv_decode3_2, [7, 7, 64, 64], phase_train, True, name="conv_decode3_3")

  dropout2_decode = tf.layers.dropout(conv_decode3_3, rate=(1-keep_prob), training=phase_train, name="dropout2_decode")
  upsample2= deconv_layer(dropout2_decode, [2, 2, 64, 64], [batch_size, FLAGS.image_h//2, FLAGS.image_w//2, 64], 2, "up2")
  conv_decode2_1 = conv_layer_with_bn(upsample2, [7, 7, 64, 64], phase_train, True, name="conv_decode2_1")
  conv_decode2_2 = conv_layer_with_bn(conv_decode2_1, [7, 7, 64, 64], phase_train, True, name="conv_decode2_2")

  dropout1_decode = tf.layers.dropout(conv_decode2_2, rate=(1-keep_prob), training=phase_train, name="dropout1_deconv")
  upsample1 = deconv_layer(dropout1_decode, [2, 2, 64, 64], [batch_size, FLAGS.image_h, FLAGS.image_w, 64], 2, "up1")
  conv_decode1_1 = conv_layer_with_bn(upsample1, [7, 7, 64, 64], phase_train, True, name="conv_decode1_1")
  conv_decode1_2 = conv_layer_with_bn(conv_decode1_1, [7, 7, 64, 64], phase_train, True, name="conv_decode1_2")
  """ End of decoder """

  """ Start Classify """
  # output predicted class number (2)
  with tf.variable_scope('conv_classifier') as scope: #all variables prefixed with "conv_classifier/"
    shape=[1, 1, 64, FLAGS.num_class]
    if(FLAGS.conv_init == "msra"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=msra_initializer(1, 64),
                                           wd=0.0005)
    elif(FLAGS.conv_init == "var_scale"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=tf.contrib.layers.variance_scaling_initializer(), #orthogonal_initializer()
                                           wd=None)
    elif(FLAGS.conv_init == "xavier"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=tf.contrib.layers.xavier_initializer(), #orthogonal_initializer()
                                           wd=None)

    conv = tf.nn.conv2d(conv_decode1_2, kernel, [1, 1, 1, 1], padding='SAME')
    biases = _variable_on_cpu('biases', [FLAGS.num_class], tf.constant_initializer(0.0))
    conv_classifier = tf.nn.bias_add(conv, biases, name=scope.name) #tf.nn.bias_add is an activation function. Simple add that specifies 1-D tensor bias
    #logit = conv_classifier = prediction
  return conv_classifier


#inference-extended
def inference_extended(images, phase_train, batch_size, keep_prob):
  """ Inference builds the graph as far as is required for running the network forward
      to make predictions.

      The arcitecure has 5 different layer sizes, each appear twice
      - once in the encoder and once in the decoder. Each "block" of layers (with the sames size)
      are of different types. For example block one has two conv-batch-relu layers and one pooling layer.

      Args:
        images: Images Tensors (placeholder with correct shape, img_h, img_w, img_d)
        phase_train:

      Returns:
        logit (scores for the classes, that sums up to 1)
  """
  # norm1 = tf.nn.lrn(images, depth_radius=5, bias=1.0, alpha=0.0001, beta=0.75,
  #                   name='norm1')
  # conv1_1 = conv_layer_with_bn(norm1, [7, 7, images.get_shape().as_list()[3], 64], phase_train, name="conv1_1")

  conv1_1 = conv_layer_with_bn(images, [7, 7, images.get_shape().as_list()[3], 64], phase_train, name="conv1_1")
  conv1_2 = conv_layer_with_bn(conv1_1, [7, 7, 64, 64], phase_train, name="conv1_2")
  pool1, pool1_indices = tf.nn.max_pool_with_argmax(conv1_2, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool1')
  conv2_1 = conv_layer_with_bn(pool1, [7, 7, 64, 64], phase_train, name="conv2_1")
  conv2_2 = conv_layer_with_bn(conv2_1, [7, 7, 64, 64], phase_train, name="conv2_2")
  pool2, pool2_indices = tf.nn.max_pool_with_argmax(conv2_2, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool2')
  conv3_1 = conv_layer_with_bn(pool2, [7, 7, 64, 64], phase_train, name="conv3_1")
  conv3_2 = conv_layer_with_bn(conv3_1, [7, 7, 64, 64], phase_train, name="conv3_2")
  conv3_3 = conv_layer_with_bn(conv3_2, [7, 7, 64, 64], phase_train, name="conv3_3")
  pool3, pool3_indices = tf.nn.max_pool_with_argmax(conv3_3, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool3')
  conv4_1 = conv_layer_with_bn(pool3, [7, 7, 64, 64], phase_train, name="conv4_1")
  conv4_2 = conv_layer_with_bn(conv4_1, [7, 7, 64, 64], phase_train, name="conv4_2")
  conv4_3 = conv_layer_with_bn(conv4_2, [7, 7, 64, 64], phase_train, name="conv4_3")
  pool4, pool4_indices = tf.nn.max_pool_with_argmax(conv4_3, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool4')
  conv5_1 = conv_layer_with_bn(pool4, [7, 7, 64, 64], phase_train, name="conv5_1")
  conv5_2 = conv_layer_with_bn(conv5_1, [7, 7, 64, 64], phase_train, name="conv5_2")
  conv5_3 = conv_layer_with_bn(conv5_2, [7, 7, 64, 64], phase_train, name="conv5_3")
  pool5, pool5_indices = tf.nn.max_pool_with_argmax(conv5_3, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool5')
  """ End of encoder """

  """ Start decoder """
  upsample5 = deconv_layer(pool5, [2, 2, 64, 64], [batch_size, FLAGS.image_h//16, FLAGS.image_w//16, 64], 2, "up5")
  conv_decode5_1 = conv_layer_with_bn(upsample5, [7, 7, 64, 64], phase_train, True, name="conv_decode5_1")
  conv_decode5_2 = conv_layer_with_bn(conv_decode5_1, [7, 7, 64, 64], phase_train, True, name="conv_decode5_2")
  conv_decode5_3 = conv_layer_with_bn(conv_decode5_2, [7, 7, 64, 64], phase_train, True, name="conv_decode5_3")

  upsample4 = deconv_layer(conv_decode5_3, [2, 2, 64, 64], [batch_size, FLAGS.image_h//8, FLAGS.image_w//8, 64], 2, "up4")
  conv_decode4_1 = conv_layer_with_bn(upsample4, [7, 7, 64, 64], phase_train, True, name="conv_decode4_1")
  conv_decode4_2 = conv_layer_with_bn(conv_decode4_1, [7, 7, 64, 64], phase_train, True, name="conv_decode4_2")
  conv_decode4_3 = conv_layer_with_bn(conv_decode4_2, [7, 7, 64, 64], phase_train, True, name="conv_decode4_3")

  upsample3 = deconv_layer(conv_decode4_3, [2, 2, 64, 64], [batch_size, FLAGS.image_h//4, FLAGS.image_w//4, 64], 2, "up3")
  conv_decode3_1 = conv_layer_with_bn(upsample3, [7, 7, 64, 64], phase_train, True, name="conv_decode3_1")
  conv_decode3_2 = conv_layer_with_bn(conv_decode3_1, [7, 7, 64, 64], phase_train, True, name="conv_decode3_2")
  conv_decode3_3 = conv_layer_with_bn(conv_decode3_2, [7, 7, 64, 64], phase_train, True, name="conv_decode3_3")

  upsample2= deconv_layer(conv_decode3_3, [2, 2, 64, 64], [batch_size, FLAGS.image_h//2, FLAGS.image_w//2, 64], 2, "up2")
  conv_decode2_1 = conv_layer_with_bn(upsample2, [7, 7, 64, 64], phase_train, True, name="conv_decode2_1")
  conv_decode2_2 = conv_layer_with_bn(conv_decode2_1, [7, 7, 64, 64], phase_train, True, name="conv_decode2_2")

  upsample1 = deconv_layer(conv_decode2_2, [2, 2, 64, 64], [batch_size, FLAGS.image_h, FLAGS.image_w, 64], 2, "up1")
  conv_decode1_1 = conv_layer_with_bn(upsample1, [7, 7, 64, 64], phase_train, True, name="conv_decode1_1")
  conv_decode1_2 = conv_layer_with_bn(conv_decode1_1, [7, 7, 64, 64], phase_train, True, name="conv_decode1_2")
  """ End of decoder """

  """ Start Classify """
  # output predicted class number (2)
  with tf.variable_scope('conv_classifier') as scope: #all variables prefixed with "conv_classifier/"
    shape=[1, 1, 64, FLAGS.num_class]
    if(FLAGS.conv_init == "msra"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=msra_initializer(1, 64),
                                           wd=0.0005)
    elif(FLAGS.conv_init == "var_scale"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           #initializer=tf.contrib.layers.xavier_initializer(), #orthogonal_initializer()
                                           initializer=tf.contrib.layers.variance_scaling_initializer(), #orthogonal_initializer()
                                           wd=None)
    elif(FLAGS.conv_init == "xavier"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=tf.contrib.layers.xavier_initializer(), #orthogonal_initializer()
                                           wd=None)

    conv = tf.nn.conv2d(conv_decode1_2, kernel, [1, 1, 1, 1], padding='SAME')
    biases = _variable_on_cpu('biases', [FLAGS.num_class], tf.constant_initializer(0.0))
    conv_classifier = tf.nn.bias_add(conv, biases, name=scope.name) #tf.nn.bias_add is an activation function. Simple add that specifies 1-D tensor bias
    #logit = conv_classifier = prediction
  return conv_classifier

#inference_full_pooling_indices
def inference_full_pooling_indices(images, phase_train, batch_size, keep_prob):
  """
  Pooling indices are used in deconvolution
  Making conv_transpose not change size by padding the input image
  """
  # norm1 = tf.nn.lrn(images, depth_radius=5, bias=1.0, alpha=0.0001, beta=0.75,
  #                   name='norm1')
  # conv1_1 = conv_layer_with_bn(norm1, [7, 7, images.get_shape().as_list()[3], 64], phase_train, name="conv1_1")

  conv1_1 = conv_layer_with_bn(images, [7, 7, images.get_shape().as_list()[3], 64], phase_train, name="conv1_1")
  conv1_2 = conv_layer_with_bn(conv1_1, [7, 7, 64, 64], phase_train, name="conv1_2")
  pool1, pool1_indices = tf.nn.max_pool_with_argmax(conv1_2, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool1')
  conv2_1 = conv_layer_with_bn(pool1, [7, 7, 64, 64], phase_train, name="conv2_1")
  conv2_2 = conv_layer_with_bn(conv2_1, [7, 7, 64, 64], phase_train, name="conv2_2")
  pool2, pool2_indices = tf.nn.max_pool_with_argmax(conv2_2, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool2')
  conv3_1 = conv_layer_with_bn(pool2, [7, 7, 64, 64], phase_train, name="conv3_1")
  conv3_2 = conv_layer_with_bn(conv3_1, [7, 7, 64, 64], phase_train, name="conv3_2")
  conv3_3 = conv_layer_with_bn(conv3_2, [7, 7, 64, 64], phase_train, name="conv3_3")
  pool3, pool3_indices = tf.nn.max_pool_with_argmax(conv3_3, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool3')
  conv4_1 = conv_layer_with_bn(pool3, [7, 7, 64, 64], phase_train, name="conv4_1")
  conv4_2 = conv_layer_with_bn(conv4_1, [7, 7, 64, 64], phase_train, name="conv4_2")
  conv4_3 = conv_layer_with_bn(conv4_2, [7, 7, 64, 64], phase_train, name="conv4_3")
  pool4, pool4_indices = tf.nn.max_pool_with_argmax(conv4_3, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool4')
  conv5_1 = conv_layer_with_bn(pool4, [7, 7, 64, 64], phase_train, name="conv5_1")
  conv5_2 = conv_layer_with_bn(conv5_1, [7, 7, 64, 64], phase_train, name="conv5_2")
  conv5_3 = conv_layer_with_bn(conv5_2, [7, 7, 64, 64], phase_train, name="conv5_3")
  pool5, pool5_indices = tf.nn.max_pool_with_argmax(conv5_3, ksize=[1, 2, 2, 1],
                                                    strides=[1, 2, 2, 1], padding='SAME', name='pool5')
  """ End of encoder """

  """ Start decoder """
  # unpool_5 = upsample_with_pool_indices(values=pool5, indices=pool5_indices, out_shape=conv5_1.get_shape(), name='unpool_5')
  upsample5 = deconv_layer(pool5, [2, 2, 64, 64], [batch_size, FLAGS.image_h//16, FLAGS.image_w//16, 64], 2, "up5")
  conv_decode5_1 = conv_layer_with_bn(upsample5, [7, 7, 64, 64], phase_train, True, name="conv_decode5_1")
  conv_decode5_2 = conv_layer_with_bn(conv_decode5_1, [7, 7, 64, 64], phase_train, True, name="conv_decode5_2")
  conv_decode5_3 = conv_layer_with_bn(conv_decode5_2, [7, 7, 64, 64], phase_train, True, name="conv_decode5_3")

  unpool_4 = upsample_with_pool_indices(values=conv_decode5_3, indices=pool4_indices, out_shape=conv4_1.get_shape(), name='unpool_4')
  upsample4 = deconv_layer(unpool_4, [2, 2, 64, 64], [batch_size, FLAGS.image_h//8, FLAGS.image_w//8, 64], 2, "up4")
  conv_decode4_1 = conv_layer_with_bn(upsample4, [7, 7, 64, 64], phase_train, True, name="conv_decode4_1")
  conv_decode4_2 = conv_layer_with_bn(conv_decode4_1, [7, 7, 64, 64], phase_train, True, name="conv_decode4_2")
  conv_decode4_3 = conv_layer_with_bn(conv_decode4_2, [7, 7, 64, 64], phase_train, True, name="conv_decode4_3")

  unpool_3 = upsample_with_pool_indices(values=conv_decode4_3, indices=pool3_indices, out_shape=conv3_1.get_shape(), name='unpool_3')
  upsample3 = deconv_layer(unpool_3, [2, 2, 64, 64], [batch_size, FLAGS.image_h//4, FLAGS.image_w//4, 64], 2, "up3")
  conv_decode3_1 = conv_layer_with_bn(upsample3, [7, 7, 64, 64], phase_train, True, name="conv_decode3_1")
  conv_decode3_2 = conv_layer_with_bn(conv_decode3_1, [7, 7, 64, 64], phase_train, True, name="conv_decode3_2")
  conv_decode3_3 = conv_layer_with_bn(conv_decode3_2, [7, 7, 64, 64], phase_train, True, name="conv_decode3_3")

  unpool_2 = upsample_with_pool_indices(values=conv_decode3_3, indices=pool2_indices, out_shape=conv2_1.get_shape(), name='unpool_2')
  upsample2= deconv_layer(unpool_2, [2, 2, 64, 64], [batch_size, FLAGS.image_h//2, FLAGS.image_w//2, 64], 2, "up2")
  conv_decode2_1 = conv_layer_with_bn(upsample2, [7, 7, 64, 64], phase_train, True, name="conv_decode2_1")
  conv_decode2_2 = conv_layer_with_bn(conv_decode2_1, [7, 7, 64, 64], phase_train, True, name="conv_decode2_2")

  unpool_1 = upsample_with_pool_indices(values=conv_decode2_2, indices=pool1_indices, out_shape=conv1_1.get_shape(), name='unpool_1')
  # upsample1 = deconv_layer(unpool_1, [2, 2, 64, 64], [batch_size, FLAGS.image_h, FLAGS.image_w, 64], 2, "up1")
  conv_decode1_1 = conv_layer_with_bn(unpool_1, [7, 7, 64, 64], phase_train, True, name="conv_decode1_1")
  conv_decode1_2 = conv_layer_with_bn(conv_decode1_1, [7, 7, 64, 64], phase_train, True, name="conv_decode1_2")
  """ End of decoder """

  """ Start Classify """
  # output predicted class number (2)
  with tf.variable_scope('conv_classifier') as scope: #all variables prefixed with "conv_classifier/"
    kernel = _variable_with_weight_decay('weights',
                                         shape=[1, 1, 64, FLAGS.num_class],
                                        #  initializer=msra_initializer(1, 64),
                                        #  initializer=tf.contrib.layers.xavier_initializer(1, 64),
                                         initializer=tf.contrib.layers.variance_scaling_initializer(),
                                         wd=0.0005)
    conv = tf.nn.conv2d(conv_decode1_2, kernel, [1, 1, 1, 1], padding='SAME')
    biases = _variable_on_cpu('biases', [FLAGS.num_class], tf.constant_initializer(0.0))
    conv_classifier = tf.nn.bias_add(conv, biases, name=scope.name) #tf.nn.bias_add is an activation function. Simple add that specifies 1-D tensor bias
    #logit = conv_classifier = prediction
  return conv_classifier

def cal_loss(logits, labels):
  """ Assigning loss_weight based on median frequncy balancing,
   and using weighted loss because of unbalanced dataset.
   High value means there are fewer instances in the dataset, and makes the instances more important."""
  loss_weight = np.array([
      FLAGS.balance_weight_0, #"Not building"
      FLAGS.balance_weight_1 #"Building"
  ])
  labels = tf.cast(labels, tf.int32)
  return weighted_loss(logits, labels, num_classes=FLAGS.num_class, head=loss_weight)


def weighted_loss(logits, labels, num_classes, head=None): #None is default value (if no other is given)
  """Calculate the loss from the logits and the labels.
  Args:
    logits: tensor, float - [batch_size, width, height, num_classes].
        Use vgg_fcn.up as logits.
    labels: Labels tensor, int32 - [batch_size, width, height, num_classes].
        The ground truth of your data.
    head: numpy array - [num_classes]
        Weighting the loss of each class
        Optional: Prioritize some classes
  Returns:
    loss: Loss tensor of type float.
  """
  with tf.name_scope('loss'):
      logits = tf.reshape(logits, (-1, num_classes))
      epsilon = tf.constant(value=1e-10)
      logits = logits + epsilon
      # construct one-hot label array
      label_flat = tf.reshape(labels, (-1, 1))
      # should be [batch ,num_classes]
      labels = tf.reshape(tf.one_hot(label_flat, depth=num_classes), (-1, num_classes))
      softmax = tf.nn.softmax(logits)
      cross_entropy = -tf.reduce_sum(tf.multiply(labels * tf.log(softmax + epsilon), head), reduction_indices=[1])
      # cross_entropy = -tf.reduce_sum(labels * tf.log(softmax + epsilon), reduction_indices=[1])

      cross_entropy_mean = tf.reduce_mean(cross_entropy, name='cross_entropy')
      tf.add_to_collection('losses', cross_entropy_mean)
      loss = tf.add_n(tf.get_collection('losses'), name='total_loss')
  return loss


def train(total_loss, global_step):

  """ Training the model
    Create an optimizer and apply to all trainable variables.
    Add moving average for all trainable variables??

    Args:
      total_loss: Total loss from cal_loss()
      global_step: Integer Variable counting the number of training steps
        processed.
    Returns:
      train_op: op for training.
  """

  """ fixed learning rate """
  tf.summary.scalar('learning_rate', FLAGS.learning_rate)

  # Generate moving averages of all losses and associated summaries.
  loss_averages_op = _add_loss_summaries(total_loss)

  # Compute gradients:
  with tf.control_dependencies([loss_averages_op]):
    if(FLAGS.optimizer == "SGD"):
      print("Running with SGD optimizer")
      opt = tf.train.GradientDescentOptimizer(
        0.1)
    elif(FLAGS.optimizer == "adam"):
      print("Running with adam optimizer")
      opt = tf.train.AdamOptimizer(
        0.001)
    elif(FLAGS.optimizer == "adagrad"):
      print("Running with adagrad optimizer")
      opt = tf.train.AdagradOptimizer(
          0.01)
    elif(FLAGS.optimizer == "momentum"):
      print("Running with momentum optimizer")
      opt = tf.train.MomentumOptimizer(
        FLAGS.learning_rate,
        momentum = 0.99,
        name = 'Momentum')
    else:
      raise ValueError("optimizer was not recognized.")

    print('total_loss')
    print(total_loss)
    grads = opt.compute_gradients(total_loss)

  apply_gradient_op = opt.apply_gradients(grads, global_step=global_step)

  # Add histograms for trainable variables.
  for var in tf.trainable_variables():
    tf.summary.histogram(var.op.name, var)

  # Add histograms for gradients.
  for grad, var in grads:
    if grad is not None:
      tf.summary.histogram(var.op.name + '/gradients', grad)

  # Track the moving averages of all trainable variables.
  variable_averages = tf.train.ExponentialMovingAverage(
      FLAGS.moving_average_decay, global_step)
  variables_averages_op = variable_averages.apply(tf.trainable_variables())

  with tf.control_dependencies([apply_gradient_op, variables_averages_op]):
    train_op = tf.no_op(name='train')

  return train_op


""" --- Initializers --- """

def msra_initializer(kl, dl):
    """
    kl for kernel size
    dl for filter number

    Truncated normal distribution
    """
    stddev = math.sqrt(2. / (kl**2 * dl))
    return tf.truncated_normal_initializer(stddev=stddev)

def orthogonal_initializer(scale = 1.1):
    ''' From Lasagne and Keras. Reference: Saxe et al., http://arxiv.org/abs/1312.6120
    '''
    print('Warning -- You have opted to use the orthogonal_initializer function')
    def _initializer(shape, dtype=tf.float32):
      flat_shape = (shape[0], np.prod(shape[1:]))
      a = np.random.normal(0.0, 1.0, flat_shape)
      u, _, v = np.linalg.svd(a, full_matrices=False)
      # pick the one with the correct shape
      q = u if u.shape == flat_shape else v
      q = q.reshape(shape) #this needs to be corrected to float32
      print('you have initialized one orthogonal matrix.')
      return tf.constant(scale * q[:shape[0], :shape[1]], dtype=tf.float32)
    return _initializer


""" ----  Summaries  -----"""

def _activation_summary(x):
  """Helper to create summaries for activations.

  Creates a summary that provides a histogram of activations.
  Creates a summary that measures the sparsity of activations.

  Args:
    x: Tensor
  Returns:
    nothing
  """
  # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU training
  # session. This helps the clarity of presentation on tensorboard.
  tensor_name = re.sub('%s_[0-9]*/' % TOWER_NAME, '', x.op.name)
  tf.summary.histogram(tensor_name + '/activations', x)
  tf.summary.scalar(tensor_name + '/sparsity', tf.nn.zero_fraction(x))


def _add_loss_summaries(total_loss):
  """Add summaries for losses.
  Generates moving average for all losses and associated summaries for
  visualizing the performance of the network.

  Args:
    total_loss: Total loss from loss().
  Returns:
    loss_averages_op: op for generating moving averages of losses.
  """
  # Compute the moving average of all individual losses and the total loss.
  loss_averages = tf.train.ExponentialMovingAverage(0.9, name='avg')
  losses = tf.get_collection('losses')
  loss_averages_op = loss_averages.apply(losses + [total_loss])

  # Attach a scalar summary to all individual losses and the total loss; do the
  # same for the averaged version of the losses.
  for l in losses + [total_loss]:
    # Name each loss as '(raw)' and name the moving average version of the loss
    # as the original loss name.
    tf.summary.scalar(l.op.name +' (raw)', l)
    tf.summary.scalar(l.op.name, loss_averages.average(l))

  return loss_averages_op


def _variable_on_cpu(name, shape, initializer):
  """Helper to create a Variable stored on CPU memory.
  Args:
    name: name of the variable
    shape: list of ints
    initializer: initializer for Variable
  Returns:
    Variable Tensor
  """
  with tf.device('/cpu:0'):
    #dtype = tf.float16 if FLAGS.use_fp16 else tf.float32 #added this after, cause it was in cifar model
    var = tf.get_variable(name, shape, initializer=initializer)#, dtype=dtype)
  return var

def _variable_with_weight_decay(name, shape, initializer, wd):
  """Helper to create an initialized Variable with weight decay.
  Note that the Variable is initialized with a truncated normal distribution.
  A weight decay is added only if one is specified.
  Args:
    name: name of the variable
    shape: list of ints
    stddev: standard deviation of a truncated Gaussian
    wd: add L2Loss weight decay multiplied by this float. If None, weight
        decay is not added for this Variable.
  Returns:
    Variable Tensor
  """
  var = _variable_on_cpu(name, shape, initializer)

  if wd is not None:
    weight_decay = tf.multiply(tf.nn.l2_loss(var), wd, name='weight_loss')
    tf.add_to_collection('losses', weight_decay)
  return var


def conv_layer_with_bn(inputT, shape, train_phase, activation=True, name=None):
  """
  Used in inference() to define conv-layers with batch normalisation and ReLu (blue box in figure).
  """
  in_channel = shape[2]
  out_channel = shape[3]
  k_size = shape[0]

  with tf.variable_scope(name) as scope:
    if(FLAGS.conv_init == "msra"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=msra_initializer(k_size, in_channel), #orthogonal_initializer()
                                           wd=0.0005)
    elif(FLAGS.conv_init == "var_scale"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           #initializer=tf.contrib.layers.xavier_initializer(), #orthogonal_initializer()
                                           initializer=tf.contrib.layers.variance_scaling_initializer(), #orthogonal_initializer()
                                           wd=None)
    elif(FLAGS.conv_init == "xavier"):
      kernel = _variable_with_weight_decay('weights',
                                           shape=shape,
                                           initializer=tf.contrib.layers.xavier_initializer(), #orthogonal_initializer()
                                           wd=None)

    conv = tf.nn.conv2d(inputT, kernel, [1, 1, 1, 1], padding='SAME')
    biases = _variable_on_cpu('biases', [out_channel], tf.constant_initializer(0.0))
    bias = tf.nn.bias_add(conv, biases)

    if activation is True: #Set to true during encoder
      conv_out = tf.nn.relu(batch_norm_layer(bias, train_phase, scope.name))
    else: #Set to false during decoder (at least originally, might change it) #TODO
      conv_out = batch_norm_layer(bias, train_phase, scope.name)

  return conv_out


def batch_norm_layer(inputT, is_training, scope):
  """Used in conv_layer_with_bn()"""
  return tf.cond(is_training,
          lambda: tf.contrib.layers.batch_norm(inputT, is_training=True,
                           center=False, updates_collections=None, scope=scope+"_bn"),
          lambda: tf.contrib.layers.batch_norm(inputT, is_training=False,
                           updates_collections=None, center=False, scope=scope+"_bn", reuse = True))


def deconv_layer(inputT, f_shape, output_shape, stride=2, name=None):
  #deconv_layer(pool5, [2, 2, 64, 64], [batch_size, FLAGS.image_h//16, FLAGS.image_w//16, 64], 2, "up5")
  """Used in inference() to create upsample layer"""
  # output_shape = [batch, width, height, channels]
  strides = [1, stride, stride, 1]
  with tf.variable_scope(name):
    weights = get_deconv_filter(f_shape)
    deconv = tf.nn.conv2d_transpose(inputT, weights, output_shape,
                                        strides=strides, padding='SAME')
  return deconv


def get_deconv_filter(f_shape):
  """
    reference: https://github.com/MarvinTeichmann/tensorflow-fcn - hvorfor?

    Used by the deconv_layer() to define the weights

    Args:
      f_shape:? example is [2, 2, 64, 64], but not sure what it defines

    Returns:
      variable: named up_filter
  """
  width = f_shape[0]
  height = f_shape[0]
  f = ceil(width/2.0)
  c = (2 * f - 1 - f % 2) / (2.0 * f)
  bilinear = np.zeros([f_shape[0], f_shape[1]])
  for x in range(width):
      for y in range(height):
          value = (1 - abs(x / f - c)) * (1 - abs(y / f - c))
          bilinear[x, y] = value
  weights = np.zeros(f_shape)
  for i in range(f_shape[2]):
      weights[:, :, i, i] = bilinear

  init = tf.constant_initializer(value=weights,
                                 dtype=tf.float32)
  return tf.get_variable(name="up_filter", initializer=init,
                         shape=weights.shape)



def upsample_with_pool_indices(values, indices, out_shape, name):
  indices_flat = tf.reshape(indices, [-1])
  out_shape = out_shape.as_list()

  """ Unravel indices - define the operations"""
  indices_flat_batches = tf.reshape(indices, [out_shape[0],-1]) #flatten each batch independently
  tot_num_indices_per_batch = out_shape[1] * out_shape[2] * out_shape[3]
  indices_per_batch = tf.to_int32(tf.divide(tot_num_indices_per_batch, 4))
  for i in range(0, out_shape[0]): #for each batch
    print(i)
    indices = indices_flat_batches[i]
    batch_dim = tf.multiply(tf.ones([indices_per_batch], tf.int64), i) #index starts with zero for every new batch
    #Finding index as if always in batch 1 -> will still have same last three dimensions
    first_dim = tf.to_int64(tf.divide(indices, (out_shape[2] * out_shape[3])))
    #finding index as if in "first" matrix - will still have same two last dimensions
    first_matr_indices = indices - (first_dim * out_shape[2] * out_shape[3])
    second_dim = tf.to_int64(tf.divide(first_matr_indices, out_shape[3]))
    third_dim = tf.subtract(first_matr_indices, (out_shape[3] * (first_matr_indices // out_shape[3]) ))
    res_index = tf.transpose([batch_dim, first_dim, second_dim, third_dim])
    if(i>0):
      unraveled_indices = tf.concat([unraveled_indices, res_index], 0)
    else:
      unraveled_indices = res_index

  values_flattened = tf.reshape(values, [-1])
  result_matrix = tf.SparseTensor(tf.to_int64(unraveled_indices), tf.to_int64(values_flattened), tf.to_int64(out_shape))
  res_matrix_dense = tf.sparse_tensor_to_dense(result_matrix, name="sparse_tensor", validate_indices=False)
  return tf.to_float(res_matrix_dense)



def upsample_test(net, stride, mode='ZEROS'):
  """
  Imitate reverse operation of Max-Pooling by either placing original max values
  into a fixed postion of upsampled cell:
  [0.9] =>[[.9, 0],   (stride=2)
           [ 0, 0]]
  or copying the value into each cell:
  [0.9] =>[[.9, .9],  (stride=2)
           [ .9, .9]]
  :param net: 4D input tensor with [batch_size, width, heights, channels] axis
  :param stride:
  :param mode: string 'ZEROS' or 'COPY' indicating which value to use for undefined cells
  :return:  4D tensor of size [batch_size, width*stride, heights*stride, channels]
  """
  assert mode in ['COPY', 'ZEROS']
  with tf.name_scope('Upsampling'):
    net = _upsample_along_axis(net, 2, stride, mode=mode)
    net = _upsample_along_axis(net, 1, stride, mode=mode)
    return net


def _upsample_along_axis(volume, axis, stride, mode='ZEROS'):
  shape = volume.get_shape().as_list()

  assert mode in ['COPY', 'ZEROS']
  assert 0 <= axis < len(shape)

  target_shape = shape[:]
  target_shape[axis] *= stride

  padding = tf.zeros(shape, dtype=volume.dtype) if mode == 'ZEROS' else volume
  parts = [volume] + [padding for _ in range(stride - 1)]
  volume = tf.concat(parts, min(axis+1, len(shape)-1))

  volume = tf.reshape(volume, target_shape)
  return volume
