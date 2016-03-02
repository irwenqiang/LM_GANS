'''
Build a simple neural language model using GRU units

So on each time you have a matrix of probabilities, p
p is a 64 x 30k matrix if we have 64 examples and 30k words

your other input is the word embeddings
either for the discriminator on the generator on the next step
your op should sample from a multinomial correspond to p,
grab the right word embeddings, and return them



'''
import theano
import theano.tensor as tensor

import cPickle as pkl
import ipdb
import numpy
import copy

import os
import time

from six.moves import xrange
from data_iterator import TextIterator
from utils import zipp, unzip, init_tparams, load_params, itemlist
import optimizers

from Descriminator import discriminator

from lm_base import (init_params, build_sampler,
                     gen_sample, pred_probs, prepare_data)

from lm_discriminator import  build_GAN_model


profile = False

def train(dim_word=100,  # word vector dimensionality
          dim=1000,  # the number of GRU units
          encoder='gru',
          patience=10,  # early stopping patience
          max_epochs=5000,
          finish_after=10000000,  # finish after this many updates
          dispFreq=100,
          decay_c=0.,  # L2 weight decay penalty
          lrate=0.01,
          n_words=100000,  # vocabulary size
          maxlen=100,  # maximum length of the description
          optimizer='rmsprop',
          batch_size=16,
          valid_batch_size=16,
          saveto='model.npz',
          validFreq=1000,
          saveFreq=1000,  # save the parameters after every saveFreq updates
          sampleFreq=100,  # generate some samples after every sampleFreq
          dataset='/data/lisatmp4/anirudhg/wiki.tok.txt.gz',
          valid_dataset='/data/lisatmp4/anirudhg/newstest2011.en.tok',
          dictionary='/data/lisatmp4/anirudhg/wiki.tok.txt.gz.pkl',
          use_dropout=False,
          reload_=False):

    # Model options
    model_options = locals().copy()

    # load dictionary
    with open(dictionary, 'rb') as f:
        worddicts = pkl.load(f)

    # invert dictionary
    worddicts_r = dict()
    for kk, vv in worddicts.iteritems():
        worddicts_r[vv] = kk

    # reload options
    if reload_ and os.path.exists(saveto):
        with open('%s.pkl' % saveto, 'rb') as f:
            model_options = pkl.load(f)

    print 'Loading data'
    train = TextIterator(dataset,
                         dictionary,
                         n_words_source=n_words,
                         batch_size=batch_size,
                         maxlen=maxlen)
    valid = TextIterator(valid_dataset,
                         dictionary,
                         n_words_source=n_words,
                         batch_size=valid_batch_size,
                         maxlen=maxlen)

    print 'Building model'
    params = init_params(model_options)

    # reload parameters
    if reload_ and os.path.exists(saveto):
        params = load_params(saveto, params)

    # create shared variables for parameters
    tparams = init_tparams(params)

    # build the symbolic computational graph

    trng, use_noise,\
        x, x_mask,\
        opt_ret,\
        cost,\
        f_get,\
        bern_dist,\
        uniform_sampling,\
        one_hot_sampled = build_GAN_model(tparams, model_options)


    inps = [x, x_mask, bern_dist, uniform_sampling]


    print 'Buliding sampler'
    f_next = build_sampler(tparams, model_options, trng)

    # before any regularizer
    print 'Building f_log_probs...',
    f_log_probs = theano.function(inps, cost, profile=profile)
    print 'Done'

    cost = cost.mean()

    # apply L2 regularization on weights
    if decay_c > 0.:
        decay_c = theano.shared(numpy.float32(decay_c), name='decay_c')
        weight_decay = 0.
        for kk, vv in tparams.iteritems():
            weight_decay += (vv ** 2).sum()
        weight_decay *= decay_c
        cost += weight_decay

    # after any regularizer - compile the computational graph for cost
    print 'Building f_cost...',
    f_cost = theano.function(inps, cost, profile=profile)
    print 'Done'

    print 'Computing gradient...',
    grads = tensor.grad(cost, wrt=itemlist(tparams))
    print 'Done'

    # compile the optimizer, the actual computational graph is compiled here
    lr = tensor.scalar(name='lr')
    print 'Building optimizers...',
    f_grad_shared, f_update = getattr(optimizers, optimizer)(lr, tparams,
                                                             grads, inps, cost)

    print 'Done'

    print 'Optimization'

    history_errs = []
    # reload history
    if reload_ and os.path.exists(saveto):
        history_errs = list(numpy.load(saveto)['history_errs'])
    best_p = None
    bad_count = 0

    if validFreq == -1:
        validFreq = len(train[0])/batch_size
    if saveFreq == -1:
        saveFreq = len(train[0])/batch_size
    if sampleFreq == -1:
        sampleFreq = len(train[0])/batch_size

    # Training loop
    uidx = 0
    estop = False
    bad_counter = 0

    d = discriminator(number_words = 30000, num_hidden = 1024, seq_length = maxlen, mb_size = 32, one_hot_input = one_hot_sampled)
    one_hot_vector_flag = d.use_one_hot_input_flag;

    import lasagne

    generator_gan_updates = lasagne.updates.adam(-1.0 * d.loss, tparams.values())

    inps_desc = [x,x_mask, bern_dist, uniform_sampling, one_hot_vector_flag, d.indices, d.target]
    train_generator_against_discriminator = theano.function(inputs = inps_desc,
                                                            outputs = {'loss' : -1.0 * d.loss},
                                                            updates = generator_gan_updates,
                                                            on_unused_input='ignore')

    print 'training gen against disc'
    for eidx in xrange(max_epochs):
        n_samples = 0

        for x in train:
            n_samples += len(x)
            uidx += 1
            use_noise.set_value(1.)

            # pad batch and create mask
            x, x_mask = prepare_data(x, maxlen=30, n_words=30000)
            if x is None:
                print 'Minibatch with zero sample under length ', maxlen
                uidx -= 1
                continue
            #print x.shape

            number_of_examples = x.shape[1]
            to_be_append = batch_size - number_of_examples
            x_temp  = x
            x_temp_new = x
            qw = x_temp[:,x.shape[1]-1]
            for i  in range(to_be_append):
                x_temp = numpy.hstack([x_temp, numpy.reshape(qw, (x.shape[0],1))])

            #print x_temp.shape
            to_be_append = maxlen  - x.shape[0]
            for i in range(to_be_append):
                x_temp  = numpy.vstack([x_temp, numpy.reshape(numpy.zeros(32), (1,32))])


            number_of_examples = x_mask.shape[1]
            to_be_append = batch_size - number_of_examples
            x_temp_mask  = x_mask
            x_temp_new_mask = x_mask
            qw = x_temp_mask[:,x_mask.shape[1]-1]
            for i  in range(to_be_append):
                x_temp_mask = numpy.hstack([x_temp_mask, numpy.reshape(qw, (x_mask.shape[0],1))])

            to_be_append = maxlen  - x_mask.shape[0]
            for i in range(to_be_append):
                x_temp_mask  = numpy.vstack([x_temp_mask, numpy.reshape(numpy.zeros(32), (1,32))])


            bern_dist = numpy.random.binomial(1, .5, size=x_temp.shape)
            uniform_sampling = numpy.random.uniform(size = x_temp.flatten().shape[0])

            d.train_real_indices(x_temp.T.astype('int32'))
            output_gen_desc = train_generator_against_discriminator(
                                             x_temp.astype('int32'),
                                             x_temp_mask.astype('float32'),
                                             bern_dist.astype('float32'),
                                             uniform_sampling.astype('float32'),
                                             1,
                                             numpy.asarray([[]]).astype('int32'),
                                             [1] * 32)
            #TODO: change hardcoded 32 to mb size

            ud_start = time.time()

            #logit_shp, logit, probs, ind_max, one_hot_sampled = f_get(x, x_mask, uniform_sampling)

            print x_temp.shape
            print x_temp_mask.shape

            # compute cost, grads and copy grads to shared variables
            cost = f_grad_shared(x_temp.astype('int32'), x_temp_mask.astype('float32'),
                                 bern_dist.astype('float32'), uniform_sampling.astype('float32'))

            # do the update on parameters
            f_update(lrate)

            ud = time.time() - ud_start

            # check for bad numbers
            if numpy.isnan(cost) or numpy.isinf(cost):
                print 'NaN detected'
                return 1.

            # verbose
            if numpy.mod(uidx, dispFreq) == 0:
                print 'Epoch ', eidx, 'Update ', uidx, 'Cost ', cost, 'UD ', ud

            # save the best model so far
            if numpy.mod(uidx, saveFreq) == 0:
                print 'Saving...',

                if best_p is not None:
                    params = best_p
                else:
                    params = unzip(tparams)
                numpy.savez(saveto, history_errs=history_errs, **params)
                pkl.dump(model_options, open('%s.pkl' % saveto, 'wb'))
                print 'Done'

            # generate some samples with the model and display them
            if numpy.mod(uidx, sampleFreq) == 0:
                # FIXME: random selection?
                gensample = [];
                for jj in xrange(32):
                    sample, score = gen_sample(tparams, f_next,
                                               model_options, trng=trng,
                                               maxlen=30, argmax=False)
                    gensample.append(sample)
                '''
                    print 'Sample ', jj, ': ',
                    ss = sample
                    for vv in ss:
                        if vv == 0:
                            break
                        if vv in worddicts_r:
                            print worddicts_r[vv],
                        else:
                            print 'UNK',
                    print
                '''
                # See wtf is going on ?
                results = prepare_data(gensample, maxlen=30, n_words=30000)
                print len(results)
                genx, genx_mask = results[0], results[1]
                #genx = genx.T
                number_of_examples = genx.shape[1]
                to_be_append = batch_size - number_of_examples

                x_temp  = genx
                x_temp_new = genx
                qw = x_temp[:,genx.shape[1]-1]

                for i  in range(to_be_append):
                    x_temp = numpy.hstack([x_temp, numpy.reshape(qw, (genx.shape[0],1))])


                to_be_append = maxlen  - x_temp.shape[0]
                for i in range(to_be_append):
                    x_temp  = numpy.vstack([x_temp, numpy.reshape(numpy.zeros(32), (1,32))])

                q =  x_temp.T
                d.train_fake_indices(q.astype('int32'))

            # validate model on validation set and early stop if necessary
            if numpy.mod(uidx, validFreq) == 0:
                use_noise.set_value(0.)
                valid_errs = pred_probs(f_log_probs, prepare_data,
                                        model_options, valid)
                valid_err = valid_errs.mean()
                history_errs.append(valid_err)

                if uidx == 0 or valid_err <= numpy.array(history_errs).min():
                    best_p = unzip(tparams)
                    bad_counter = 0
                if len(history_errs) > patience and valid_err >= \
                        numpy.array(history_errs)[:-patience].min():
                    bad_counter += 1
                    if bad_counter > patience:
                        print 'Early Stop!'
                        estop = True
                        break

                if numpy.isnan(valid_err):
                    ipdb.set_trace()

                print 'Valid ', valid_err

            # finish after this many updates
            if uidx >= finish_after:
                print 'Finishing after %d iterations!' % uidx
                estop = True
                break

        print 'Seen %d samples' % n_samples

        if estop:
            break

    if best_p is not None:
        zipp(best_p, tparams)

    use_noise.set_value(0.)
    valid_err = pred_probs(f_log_probs, prepare_data,
                           model_options, valid).mean()

    print 'Valid ', valid_err

    params = copy.copy(best_p)
    numpy.savez(saveto, zipped_params=best_p,
                history_errs=history_errs,
                **params)

    return valid_err


if __name__ == '__main__':
    pass