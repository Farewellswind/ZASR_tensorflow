#!/usr/bin/env python
# coding=utf-8

import time
import numpy as np
import tensorflow as tf
from tensorflow.python.ops import ctc_ops

from utils import utils
from conf import hyparam, config
from model_utils import network

class BiRNN(object):
    '''
    Class to init model with:
    wav_files: path to wav files
    text_labels: transcript for wav files
    words_size: the size of vocab
    words : a list for vocab
    mZword_num_map: a map dict from word to num
    '''
    def __init__(self, wav_files, text_labels, words_size, words, word_num_map):
        self.hyparam = hyparam.Config()
        self.conf = config.Config()
        self.wav_files = wav_files
        self.text_labels = text_labels
        self.words_size = words_size
        self.words = words
        self.word_num_map = word_num_map

    def add_placeholders(self):
        # input tensor for log filter or MFCC features
        self.input_tensor = tf.placeholder( tf.float32,
                                          [None, None, self.hyparam.n_input + (2 * self.hyparam.n_input * self.hyparam.n_context)],
                                          name='input')
        self.text = tf.sparse_placeholder(tf.int32, name='text')
        self.seq_length = tf.placeholder(tf.int32, [None], name='seq_length')
        self.keep_dropout = tf.placeholder(tf.float32)

    def bi_rnn_layer(self):
        '''
        建立网络模型
        :param batch_x:
        :param seq_length:
        :param n_input:
        :param self.hyparam.n_context:
        :param n_character:
        :param keep_dropout:
        '''

        batch_x = self.input_tensor
        seq_length = self.seq_length
        n_character = self.words_size + 1 
        keep_dropout = self.keep_dropout
                      
        # batch_x_shape: [self.hyparam.batch_size, n_steps, self.hyparam.n_input + 2*self.hyparam.n_input*self.hyparam.n_context]
        batch_x_shape = tf.shape(batch_x)
                      
        # 将输入转成时间序列优先
        batch_x = tf.transpose(batch_x, [1, 0, 2])
        # 再转成2维传入第一层
        batch_x = tf.reshape(batch_x,
                             [-1,
                              self.hyparam.n_input + 2 * self.hyparam.n_input * self.hyparam.n_context])  # (n_steps*self.hyparam.batch_size, self.hyparam.n_input + 2*self.hyparam.n_input*self.hyparam.n_context)
                      
        # 1st layer        
        with tf.name_scope('layer1'):
            b1 = self.variable_on_device('b1', [self.hyparam.n_hidden_1], tf.random_normal_initializer(stddev=self.hyparam.b_stddev))
            h1 = self.variable_on_device('h1', [self.hyparam.n_input + 2 * self.hyparam.n_input * self.hyparam.n_context, self.hyparam.n_hidden_1],
                                         tf.random_normal_initializer(stddev=self.hyparam.h_stddev))
            layer_1 = tf.minimum(tf.nn.relu(tf.add(tf.matmul(batch_x, h1), b1)), self.hyparam.relu_clip)
            layer_1 = tf.nn.dropout(layer_1, keep_dropout)
                      
        # 2nd layer   
        with tf.name_scope('layer2'):
            b2 = self.variable_on_device('b2', [self.hyparam.n_hidden_2], tf.random_normal_initializer(stddev=self.hyparam.b_stddev))
            h2 = self.variable_on_device('h2', [self.hyparam.n_hidden_1, self.hyparam.n_hidden_2], tf.random_normal_initializer(stddev=self.hyparam.h_stddev))
            layer_2 = tf.minimum(tf.nn.relu(tf.add(tf.matmul(layer_1, h2), b2)), self.hyparam.relu_clip)
            layer_2 = tf.nn.dropout(layer_2, keep_dropout)

        # 3rd layer
        with tf.name_scope('layer3'):
            b3 = self.variable_on_device('b3', [self.hyparam.n_hidden_3], tf.random_normal_initializer(stddev=self.hyparam.b_stddev))
            h3 = self.variable_on_device('h3', [self.hyparam.n_hidden_2, self.hyparam.n_hidden_3], tf.random_normal_initializer(stddev=self.hyparam.h_stddev))
            layer_3 = tf.minimum(tf.nn.relu(tf.add(tf.matmul(layer_2, h3), b3)), self.hyparam.relu_clip)
            layer_3 = tf.nn.dropout(layer_3, keep_dropout)
           
        # 双向rnn 
        with tf.name_scope('birnn'):
            # 前向
            lstm_fw_cell = tf.contrib.rnn.BasicLSTMCell(self.hyparam.n_cell_dim, forget_bias=1.0, state_is_tuple=True)
            lstm_fw_cell = tf.contrib.rnn.DropoutWrapper(lstm_fw_cell,
                                                         input_keep_prob=keep_dropout)
            # 后向
            lstm_bw_cell = tf.contrib.rnn.BasicLSTMCell(self.hyparam.n_cell_dim, forget_bias=1.0, state_is_tuple=True)
            lstm_bw_cell = tf.contrib.rnn.DropoutWrapper(lstm_bw_cell,
                                                         input_keep_prob=keep_dropout)
           
            # `layer_3`  `[n_steps, self.hyparam.batch_size, 2*self.hyparam.n_cell_dim]`
            # print(batch_x_shape[0])
            # print(batch_x_shape[1])
            # print(batch_x_shape[2])
            layer_3 = tf.reshape(layer_3, [-1, batch_x_shape[0], self.hyparam.n_hidden_3])
            outputs, output_states = tf.nn.bidirectional_dynamic_rnn(cell_fw=lstm_fw_cell,
                                                                     cell_bw=lstm_bw_cell,
                                                                     inputs=layer_3,
                                                                     dtype=tf.float32,
                                                                     time_major=True,
                                                                     sequence_length=seq_length)

           # 连接正反向结果[n_steps, self.hyparam.batch_size, 2*self.hyparam.n_cell_dim]
            outputs = tf.concat(outputs, 2)
            # to a single tensor of shape [n_steps*self.hyparam.batch_size, 2*self.hyparam.n_cell_dim]
            outputs = tf.reshape(outputs, [-1, 2 * self.hyparam.n_cell_dim])
 
        with tf.name_scope('layer5'):
            b5 = self.variable_on_device('b5', [self.hyparam.n_hidden_5], tf.random_normal_initializer(stddev=self.hyparam.b_stddev))
            h5 = self.variable_on_device('h5', [(2 * self.hyparam.n_cell_dim), self.hyparam.n_hidden_5],
                                         tf.random_normal_initializer(stddev=self.hyparam.h_stddev))
            layer_5 = tf.minimum(tf.nn.relu(tf.add(tf.matmul(outputs, h5), b5)), self.hyparam.relu_clip)
            layer_5 = tf.nn.dropout(layer_5, keep_dropout)
 
        with tf.name_scope('layer6'):
            # 全连接层用于softmax分类
            b6 = self.variable_on_device('b6', [n_character], tf.random_normal_initializer(stddev=self.hyparam.b_stddev))
            h6 = self.variable_on_device('h6', [self.hyparam.n_hidden_5, n_character], tf.random_normal_initializer(stddev=self.hyparam.h_stddev))
            layer_6 = tf.add(tf.matmul(layer_5, h6), b6)
 
        # 将2维[n_steps*self.hyparam.batch_size, n_character]转成3维 time-major [n_steps, self.hyparam.batch_size, n_character].
        layer_6 = tf.reshape(layer_6, [-1, batch_x_shape[0], n_character])
 
        # Output shape: [n_steps, self.hyparam.batch_size, n_character]
        print "layer_6: ", np.shape(layer_6), layer_6
        self.logits = layer_6

    def deepspeech2(self):
        '''
        BUild a network with CNN-BRNN-Lookahead CNN -FC.
        '''
        batch_x = self.input_tensor
        seq_length = self.seq_length
        n_character = self.words_size + 1
        keep_dropout = self.keep_dropout
        n_input = self.hyparam.n_input
        n_context = self.hyparam.n_context

        batch_x_shape = tf.shape(batch_x)
        batch_x = tf.transpose(batch_x, [1, 0, 2])
        print "batch_x: ", batch_x
        batch_x = tf.expand_dims(batch_x, -1)
        batch_x = tf.reshape(batch_x, 
                            [self.hyparam.batch_size, -1, n_input + 2 * n_input * n_context, 1] ) # shape (batch_size, ?, 494, 1)


        with tf.variable_scope('conv_1'):
            # usage: conv2d(batch_x, filter_shape, pool_size, hyparam, use_dropout=False)
            conv_1 = network.conv2d(batch_x, 
                                    [1,  n_input + 2 * n_input * n_context, 1,  n_input + 2 * n_input * n_context], 
                                    1, self.hyparam, use_dropout=True) # shape (batch_size, ?, 1, 494)
            conv_1 = tf.squeeze(conv_1, [2])

            conv_1 = tf.reshape(conv_1, [-1, self.hyparam.batch_size, n_input + 2 * n_input * n_context]) # (batch_size * ?, 494)

        with tf.variable_scope('birnn_1'):
            birnn_1 = network.BiRNN(conv_1, seq_length, self.hyparam )
            print "birnn_1: ", birnn_1
        with tf.variable_scope('birnn_2'):
            birnn_2 = network.BiRNN(birnn_1, seq_length, self.hyparam )

        with tf.variable_scope('birnn_3'):
            birnn_3 = network.BiRNN(birnn_2, seq_length, self.hyparam, use_dropout=True)
        
        with tf.variable_scope('lcnn_1'):
#            lcnn_1 = network.lookahead_cnn(birnn_3, [2, 2*self.hyparam.n_cell_dim, 1, 2*self.hyparam.n_cell_dim], 2, seq_length, self.hyparam, use_dropout=True)
            birnn_3 = tf.reshape(birnn_3, [self.hyparam.batch_size, -1, 2*self.hyparam.n_cell_dim])
            birnn_3 = tf.expand_dims(birnn_3, -1)
            lcnn_1 = network.conv2d(birnn_3, [2, 2*self.hyparam.n_cell_dim, 1, 2*self.hyparam.n_cell_dim], 1, self.hyparam, use_dropout=True)
            lcnn_1 = tf.squeeze(lcnn_1,[2])
            lcnn_1 = tf.reshape(lcnn_1, [-1, 2 * self.hyparam.n_cell_dim])

        with tf.variable_scope('fc'):
            b_fc = self.variable_on_device('b_fc', [n_character], tf.random_normal_initializer(stddev=self.hyparam.b_stddev))
            h_fc = self.variable_on_device('h_fc', 
                                           [2 * self.hyparam.n_cell_dim, n_character],
                                           tf.random_normal_initializer(stddev=self.hyparam.h_stddev))
            layer_fc = tf.add(tf.matmul(lcnn_1, h_fc), b_fc)
            # turn it to 3 dim, [n_steps, hyparam.batch_size, n_character]
#            layer_fc = tf.reshape(layer_fc, [-1, batch_x_shape[0], n_character])
            layer_fc = tf.reshape(layer_fc, [-1, self.hyparam.batch_size, n_character])
        print "layer_fc: ", np.shape(layer_fc), layer_fc
        self.logits = layer_fc

    def loss(self):      
        """              
        定义loss         
        :return:         
        """              
        # 调用ctc loss   
        with tf.name_scope('loss'): #损失
            print "self.text: ", self.text, "self.logits: ", self.logits, "self.seq_length: ", self.seq_length
            self.avg_loss = tf.reduce_mean(ctc_ops.ctc_loss(self.text, self.logits, self.seq_length))
            tf.summary.scalar('loss',self.avg_loss)
        # [optimizer]    
        with tf.name_scope('train'): #训练过程
            self.optimizer = tf.train.AdamOptimizer(learning_rate=self.hyparam.learning_rate).minimize(self.avg_loss)
                         
        with tf.name_scope("decode"):
            self.decoded, log_prob = ctc_ops.ctc_beam_search_decoder(self.logits, self.seq_length, merge_repeated=False)
                         
        with tf.name_scope("accuracy"):
            self.distance = tf.edit_distance(tf.cast(self.decoded[0], tf.int32), self.text)
            # 计算label error rate (accuracy)
            self.label_err = tf.reduce_mean(self.distance, name='label_error_rate')
            tf.summary.scalar('accuracy', self.label_err)

    def get_feed_dict(self, dropout=None):
        """         
        定义变量    
        :param dropout: 
        :return:    
        """         
        feed_dict = {self.input_tensor: self.audio_features,
                     self.text: self.sparse_labels,
                     self.seq_length: self.audio_features_len}
                    
        if dropout != None:
            feed_dict[self.keep_dropout] = dropout
        else:       
            feed_dict[self.keep_dropout] = self.hyparam.keep_dropout_rate
                    
        return feed_dict
                    
    def init_session(self):
        self.savedir = self.conf.get("FILE_DATA").savedir
        self.saver = tf.train.Saver(max_to_keep=1)  # 生成saver
        # create the session
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.7)
        self.sess = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))
        # sess = tf.Session()
        # 没有模型的话，就重新初始化
        self.sess.run(tf.global_variables_initializer())
                    
        ckpt = tf.train.latest_checkpoint(self.savedir)
        print "ckpt:", ckpt
        self.startepo = 0
        if ckpt != None:
            self.saver.restore(self.sess, ckpt)
            ind = ckpt.rfind("-")
            self.startepo = int(ckpt[ind + 1:])
            print(self.startepo)
        print()           
                          
    def add_summary(self):
        self.merged = tf.summary.merge_all()
        self.writer = tf.summary.FileWriter(self.conf.get("FILE_DATA").tensorboardfile, self.sess.graph)
                          
    def train(self):      
        epochs = 120      
                          
        # 准备运行训练步骤
        section = '\n{0:=^40}\n'
        print section.format('开始训练')
                          
        train_start = time.time()
        for epoch in range(epochs):  # 样本集迭代次数
            epoch_start = time.time()
            if epoch < self.startepo:
                continue  
                          
            print "第：", epoch, " 次迭代，一共要迭代 ", epochs, "次"
            #######################run batch####
            n_batches_epoch = int(np.ceil(len(self.text_labels) / self.hyparam.batch_size))
            print "在本次迭代中一共循环： ", n_batches_epoch, "每次取：", self.hyparam.batch_size
                          
            train_cost = 0
            train_err = 0
            next_idx = 0  

            for batch in range(n_batches_epoch):  # 一次self.hyparam.batch_size，取多少次
                # 取数据
                # temp_next_idx, temp_audio_features, temp_audio_features_len, temp_sparse_labels
                next_idx, self.audio_features, self.audio_features_len, self.sparse_labels, wav_files = utils.next_batch(
                    next_idx,
                    self.hyparam.batch_size,
                    self.hyparam.n_input,
                    self.hyparam.n_context,
                    self.text_labels,
                    self.wav_files,
                    self.word_num_map)
 
                # 计算 avg_loss optimizer ;
                batch_cost, _ = self.sess.run([self.avg_loss, self.optimizer], feed_dict=self.get_feed_dict())
                train_cost += batch_cost
 
                if (batch + 1) % 70 == 0:
                    rs = self.sess.run(self.merged, feed_dict=self.get_feed_dict())
                    self.writer.add_summary(rs, batch)
 
                    print '循环次数:', batch, '损失: ', train_cost / (batch + 1)
 
                    d, train_err = self.sess.run([self.decoded[0], self.label_err], feed_dict=self.get_feed_dict(dropout=1.0))
                    dense_decoded = tf.sparse_tensor_to_dense(d, default_value=-1).eval(session=self.sess)
                    dense_labels = utils.trans_tuple_to_texts_ch(self.sparse_labels, self.words)
 
                    print '错误率: ', train_err
                    for orig, decoded_array in zip(dense_labels, dense_decoded):
                        # convert to strings
                        decoded_str = utils.trans_array_to_text_ch(decoded_array, self.words)
                        print '语音原始文本: ', orig
                        print '识别出来的文本: ', decoded_str
                        break
 
            epoch_duration = time.time() - epoch_start
            log = '迭代次数 {}/{}, 训练损失: {:.3f}, 错误率: {:.3f}, time: {:.2f} sec'
            print(log.format(epoch, epochs, train_cost, train_err, epoch_duration))
            self.saver.save(self.sess, self.savedir + self.conf.get("FILE_DATA").savefile, global_step=epoch)
                                   
        train_duration = time.time() - train_start
        print('Training complete, total duration: {:.2f} min'.format(train_duration / 60))
        self.sess.close()          
                                   
    def test(self):                
        index = 0                  
        next_idx = 20              
                                   
        for index in range(10):    
           next_idx, self.audio_features, self.audio_features_len, self.sparse_labels, wav_files = utils.next_batch(
               next_idx,           
               1,                  
               self.hyparam.n_input,            
               self.hyparam.n_context,          
               self.text_labels,   
               self.wav_files,     
               self.word_num_map)  
                                   
           print '读入语音文件: ', wav_files[0]
           print '开始识别语音数据......'
                                   
           d, train_ler = self.sess.run([self.decoded[0], self.label_err], feed_dict=self.get_feed_dict(dropout=1.0))
           dense_decoded = tf.sparse_tensor_to_dense(d, default_value=-1).eval(session=self.sess)
           dense_labels = utils.trans_tuple_to_texts_ch(self.sparse_labels, self.words)
                                   
           for orig, decoded_array in zip(dense_labels, dense_decoded):
               # 转成string        
               decoded_str = utils.trans_array_to_text_ch(decoded_array, self.words)
               print '语音原始文本: ', orig
               print '识别出来的文本:  ', decoded_str
               break               
        self.sess.close()
         
    def test_target_wav_file(self, wav_files, txt_labels):
        print '读入语音文件: ', wav_files[0]
        print '开始识别语音数据......'
         
        self.audio_features, self.audio_features_len, text_vector, text_vector_len = utils.get_audio_mfcc_features(
            None,
            wav_files,
            self.hyparam.n_input,
            self.hyparam.n_context,
            self.word_num_map,
            txt_labels)
        self.sparse_labels = utils.sparse_tuple_from(text_vector)
        d, train_ler = self.sess.run([self.decoded[0], self.label_err], feed_dict=self.get_feed_dict(dropout=1.0))
        dense_decoded = tf.sparse_tensor_to_dense(d, default_value=-1).eval(session=self.sess)
        decoded_str = utils.trans_array_to_text_ch(dense_decoded[0], self.words)
        print '语音原始文本: ', txt_labels[0]
        print '识别出来的文本:  ', decoded_str
         
        self.sess.close()
         
    def build_train(self):
        self.add_placeholders()
#        self.bi_rnn_layer()
        self.deepspeech2()
        self.loss()
        self.init_session()
        self.add_summary()
        self.train()

    def build_test(self):
        self.add_placeholders()    
        self.bi_rnn_layer()
        self.loss() 
        self.init_session()
        self.test() 
                    
    def build_target_wav_file_test(self, wav_files, txt_labels):
        self.add_placeholders()
        self.bi_rnn_layer()
        self.loss() 
        self.init_session()
        self.test_target_wav_file(wav_files, txt_labels)
                    
    def variable_on_device(self, name, shape, initializer):
        with tf.device('/gpu:0'):
            var = tf.get_variable(name=name, shape=shape, initializer=initializer)
        return var  


