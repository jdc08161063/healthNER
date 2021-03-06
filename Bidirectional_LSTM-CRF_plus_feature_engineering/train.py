#!/usr/bin/env python

import os
import numpy as np
import optparse
import itertools
from collections import OrderedDict
from utils import create_input
import loader

from utils import models_path, evaluate, eval_script, eval_temp
from loader import word_mapping, char_mapping, POStag_mapping, cluster_mapping, tag_mapping
from loader import update_tag_scheme, prepare_dataset
from loader import augment_with_pretrained
from model import Model



# Read parameters from command line
optparser = optparse.OptionParser()
optparser.add_option(
    "-T", "--train", default="",
    help="Train set location"
)
optparser.add_option(
    "-d", "--dev", default="",
    help="Dev set location"
)
optparser.add_option(
    "-t", "--test", default="",
    help="Test set location"
)
optparser.add_option(
    "-s", "--tag_scheme", default="iob",
    help="Tagging scheme (IOB or IOBES)"
)
optparser.add_option(
    "-l", "--lower", default="0",
    type='int', help="Lowercase words (this will not affect character inputs)"
)
optparser.add_option(
    "-z", "--zeros", default="0",
    type='int', help="Replace digits with 0"
)
optparser.add_option(
    "-c", "--char_dim", default="25",
    type='int', help="Char embedding dimension"
)
optparser.add_option(
    "-C", "--char_lstm_dim", default="25",
    type='int', help="Char LSTM hidden layer size"
)
optparser.add_option(
    "-b", "--char_bidirect", default="1",
    type='int', help="Use a bidirectional LSTM for chars"
)
optparser.add_option(
    "-w", "--word_dim", default="600",
    type='int', help="Token embedding dimension"
)
optparser.add_option(
    "-W", "--word_lstm_dim", default="100",
    type='int', help="Token LSTM hidden layer size"
)
optparser.add_option(
    "-B", "--word_bidirect", default="1",
    type='int', help="Use a bidirectional LSTM for words"
)
optparser.add_option(
    "-p", "--pre_emb", default="pre_trainedVectorEmbeddings/pre_embe_glove_clean_mimic_600.txt",
    help="Location of pretrained embeddings"
)
optparser.add_option(
    "-A", "--all_emb", default="0",
    type='int', help="Load all embeddings"
)
optparser.add_option(
    "-M", "--morph_features_dim", default="1",
    type='int', help="MorphologicalFeatures, dimension x 7 (0 to disable)"
)
optparser.add_option(
    "-S", "--semantic_features_dim", default="1",
    type='int', help="SemanticFeatures, dimension x 7 (0 to disable)"
)
optparser.add_option(
    "-f", "--crf", default="0",
    type='int', help="Use CRF (0 to disable)"
)
optparser.add_option(
    "-D", "--dropout", default="0.5",
    type='float', help="Droupout on the input (0 = no dropout)"
)
optparser.add_option(
    "-L", "--lr_method", default="sgd-lr_.005",
    help="Learning method (SGD, Adadelta, Adam..)"
)
optparser.add_option(
    "-r", "--reload", default="0",
    type='int', help="Reload the last saved model"
)
opts = optparser.parse_args()[0]

# Parse parameters
parameters = OrderedDict()
parameters['tag_scheme'] = opts.tag_scheme
parameters['lower'] = opts.lower == 1
parameters['zeros'] = opts.zeros == 1
parameters['char_dim'] = opts.char_dim
parameters['char_lstm_dim'] = opts.char_lstm_dim
parameters['char_bidirect'] = opts.char_bidirect == 1
parameters['word_dim'] = opts.word_dim
parameters['word_lstm_dim'] = opts.word_lstm_dim
parameters['word_bidirect'] = opts.word_bidirect == 1
parameters['pre_emb'] = opts.pre_emb
parameters['all_emb'] = opts.all_emb == 1
parameters['morph_features_dim'] = opts.morph_features_dim
parameters['semantic_features_dim'] = opts.semantic_features_dim
parameters['crf'] = opts.crf == 1
parameters['dropout'] = opts.dropout
parameters['lr_method'] = opts.lr_method

# Check parameters validity
assert os.path.isfile(opts.train)
assert os.path.isfile(opts.dev)
assert os.path.isfile(opts.test)
assert parameters['char_dim'] > 0 or parameters['word_dim'] > 0
assert 0. <= parameters['dropout'] < 1.0
assert parameters['tag_scheme'] in ['iob', 'iobes']
assert not parameters['all_emb'] or parameters['pre_emb'] #One of the two needs to be false
assert not parameters['pre_emb'] or parameters['word_dim'] > 0
assert not parameters['pre_emb'] or os.path.isfile(parameters['pre_emb'])

# Check evaluation script / folders
if not os.path.isfile(eval_script):
    raise Exception('CoNLL evaluation script not found at "%s"' % eval_script)
if not os.path.exists(eval_temp):
    os.makedirs(eval_temp)
if not os.path.exists(models_path):
    os.makedirs(models_path)

# Initialize model
model = Model(parameters=parameters, models_path=models_path)
print "Model location: %s" % model.model_path

# Data parameters
lower = parameters['lower']
zeros = parameters['zeros']
tag_scheme = parameters['tag_scheme']

# Load sentences
train_sentences = loader.load_sentences(opts.train, lower, zeros)
dev_sentences = loader.load_sentences(opts.dev, lower, zeros)
test_sentences = loader.load_sentences(opts.test, lower, zeros)

# Use selected tagging scheme (IOB / IOBES)
update_tag_scheme(train_sentences, tag_scheme)
update_tag_scheme(dev_sentences, tag_scheme)
update_tag_scheme(test_sentences, tag_scheme)

# Create a dictionary / mapping of words
# If we use pretrained embeddings, we add them to the dictionary.
#augment_with_pretrained adds words that aren't in the dictionary, initializing them with an ID
if parameters['pre_emb']:
    dico_words_train = word_mapping(train_sentences, lower)[0]
    dico_words, word_to_id, id_to_word = augment_with_pretrained(
        dico_words_train.copy(),
        parameters['pre_emb'],
        list(itertools.chain.from_iterable(
            [[w[0] for w in s] for s in dev_sentences + test_sentences])
        ) if not parameters['all_emb'] else None
    )
else:
    dico_words, word_to_id, id_to_word = word_mapping(train_sentences, lower)
    dico_words_train = dico_words

# Create a dictionary and a mapping for words / POS tags / tags
dico_chars, char_to_id, id_to_char = char_mapping(train_sentences)
dico_POStags, POStag_to_id, id_to_POStag=POStag_mapping(train_sentences)
dico_cluster, cluster_to_id, id_to_cluster=cluster_mapping(train_sentences)
dico_tags, tag_to_id, id_to_tag = tag_mapping(train_sentences)

print "Calling the prepare_dataset :--"
# Index data
#Prepare the data correctly to use it later (not in the build model!!!)
train_data = prepare_dataset(
    train_sentences, word_to_id, char_to_id, POStag_to_id, cluster_to_id, tag_to_id, lower
)
dev_data = prepare_dataset(
    dev_sentences, word_to_id, char_to_id, POStag_to_id, cluster_to_id, tag_to_id, lower
)
test_data = prepare_dataset(
    test_sentences, word_to_id, char_to_id, POStag_to_id, cluster_to_id, tag_to_id, lower
)

print "%i / %i / %i sentences in train / dev / test." % (
    len(train_data), len(dev_data), len(test_data))

# Save the mappings to disk
print 'Saving the mappings to disk...'
model.save_mappings(id_to_word, id_to_char, id_to_POStag, id_to_cluster, id_to_tag)

# Build the model
f_train, f_eval = model.build(**parameters)

# Reload previous model values
if opts.reload:
    print 'Reloading previous model...'
    model.reload()

#
# Train network
#
singletons = set([word_to_id[k] for k, v
                  in dico_words_train.items() if v == 1])
n_epochs = 100  # number of epochs over the training set
freq_eval = 1000  # evaluate on dev every freq_eval steps
best_dev = -np.inf
best_test = -np.inf
count = 0
for epoch in xrange(n_epochs):
    epoch_costs = []
    print "Starting epoch %i..." % epoch
    for i, index in enumerate(np.random.permutation(len(train_data))):
        count += 1
        input = create_input(train_data[index], parameters, True, singletons)
        new_cost = f_train(*input)
        epoch_costs.append(new_cost)
        if i % 50 == 0 and i > 0 == 0:
            print "%i, cost average: %f" % (i, np.mean(epoch_costs[-50:]))
        if count % freq_eval == 0:
            dev_score,dev_eval_lines_prob = evaluate(parameters, f_eval, dev_sentences,
                                 dev_data, id_to_tag, dico_tags)
            test_score,test_eval_lines_prob = evaluate(parameters, f_eval, test_sentences,
                                  test_data, id_to_tag, dico_tags)
            print "Score on dev: %.5f" % dev_score
            print "Score on test: %.5f" % test_score
            if dev_score > best_dev:
                best_dev = dev_score
                best_dev_eval_lines=dev_eval_lines_prob
                test_with_best_dev=test_score
                print "New best score on dev."
                print "Saving model to disk..."
                model.save()
            if test_score > best_test:
                best_test = test_score
                best_test_eval_lines=test_eval_lines_prob
                print "New best score on test."
    print "Epoch %i done. Average cost: %f" % (epoch, np.mean(epoch_costs))

final_dev_lines=""
for line in best_dev_eval_lines:
    final_dev_lines=final_dev_lines+"\n"+line
filename=open('results/1_CCE_noCRF_dev_scores_glove_mimic_600_plus_features_double.txt','w')
filename.write(str(final_dev_lines))
filename.write("Test with best dev: "+str(test_with_best_dev))
filename.close()

final_test_lines=""
for line2 in best_test_eval_lines:
    final_test_lines=final_test_lines+"\n"+line2
filename=open('results/1_MedLine_noCRF_test_scores_glove_mimic_600_plus_features_double.txt','w')
filename.write(final_test_lines)
filename.close()

#I need to create a text describing the experiments in the results
filename=open('results/1_CCE_noCRF_features_double_plus_glove_mimic_600_description.txt','w')
filename.write('Experiment 1:\n')
filename.write('-CCE\n')
filename.write('-Bidirectional LSTM\n')
filename.write('-Glove mimic best embeddings (600)\n')
filename.write('-lstm_word (100)\n')
filename.write('-Character level embeddings (25)\n')
filename.write('-lstm_char (25)\n')
filename.write('-dropout (0.5)\n')
filename.write('-tag_scheme (IOB)\n')
filename.write('-learning method (sgd-lr_.005)\n')
filename.write('-Features\n')
filename.close()











