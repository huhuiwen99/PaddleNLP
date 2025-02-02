# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import time
from typing import Optional, Tuple
from collections import OrderedDict

import paddle
import paddle.nn as nn
import paddle.tensor as tensor
import paddle.nn.functional as F

from .. import PretrainedModel, register_base_model

__all__ = [
    'ElectraModel', 'ElectraPretrainedModel', 'ElectraForTotalPretraining',
    'ElectraDiscriminator', 'ElectraGenerator', 'ElectraClassificationHead',
    'ElectraForSequenceClassification', 'ElectraForTokenClassification',
    'ElectraPretrainingCriterion'
]


def get_activation(activation_string):
    if activation_string in ACT2FN:
        return ACT2FN[activation_string]
    else:
        raise KeyError("function {} not found in ACT2FN mapping {}".format(
            activation_string, list(ACT2FN.keys())))


def mish(x):
    return x * F.tanh(F.softplus(x))


def linear_act(x):
    return x


def swish(x):
    return x * F.sigmoid(x)


ACT2FN = {
    "relu": F.relu,
    "gelu": F.gelu,
    "tanh": F.tanh,
    "sigmoid": F.sigmoid,
    "mish": mish,
    "linear": linear_act,
    "swish": swish,
}


class ElectraEmbeddings(nn.Layer):
    """Construct the embeddings from word, position and token_type embeddings."""

    def __init__(self, vocab_size, embedding_size, hidden_dropout_prob,
                 max_position_embeddings, type_vocab_size):
        super(ElectraEmbeddings, self).__init__()
        self.word_embeddings = nn.Embedding(vocab_size, embedding_size)
        self.position_embeddings = nn.Embedding(max_position_embeddings,
                                                embedding_size)
        self.token_type_embeddings = nn.Embedding(type_vocab_size,
                                                  embedding_size)

        self.layer_norm = nn.LayerNorm(embedding_size, epsilon=1e-12)
        self.dropout = nn.Dropout(hidden_dropout_prob)

    def forward(self, input_ids, token_type_ids=None, position_ids=None):
        if position_ids is None:
            ones = paddle.ones_like(input_ids, dtype="int64")
            seq_length = paddle.cumsum(ones, axis=-1)
            position_ids = seq_length - ones
            position_ids.stop_gradient = True

        if token_type_ids is None:
            token_type_ids = paddle.zeros_like(input_ids, dtype="int64")

        input_embeddings = self.word_embeddings(input_ids)
        position_embeddings = self.position_embeddings(position_ids)
        token_type_embeddings = self.token_type_embeddings(token_type_ids)

        embeddings = input_embeddings + position_embeddings + token_type_embeddings
        embeddings = self.layer_norm(embeddings)
        embeddings = self.dropout(embeddings)
        return embeddings


class ElectraDiscriminatorPredictions(nn.Layer):
    """Prediction layer for the discriminator, made up of two dense layers."""

    def __init__(self, hidden_size, hidden_act):
        super(ElectraDiscriminatorPredictions, self).__init__()

        self.dense = nn.Linear(hidden_size, hidden_size)
        self.dense_prediction = nn.Linear(hidden_size, 1)
        self.act = get_activation(hidden_act)

    def forward(self, discriminator_hidden_states):
        hidden_states = self.dense(discriminator_hidden_states)
        hidden_states = self.act(hidden_states)
        logits = self.dense_prediction(hidden_states).squeeze()

        return logits


class ElectraGeneratorPredictions(nn.Layer):
    """Prediction layer for the generator, made up of two dense layers."""

    def __init__(self, embedding_size, hidden_size, hidden_act):
        super(ElectraGeneratorPredictions, self).__init__()

        self.layer_norm = nn.LayerNorm(embedding_size)
        self.dense = nn.Linear(hidden_size, embedding_size)
        self.act = get_activation(hidden_act)

    def forward(self, generator_hidden_states):
        hidden_states = self.dense(generator_hidden_states)
        hidden_states = self.act(hidden_states)
        hidden_states = self.layer_norm(hidden_states)

        return hidden_states


class ElectraPretrainedModel(PretrainedModel):
    """
    An abstract class to handle weights initialization and a simple interface for downloading and loading pretrained
    models.
    """
    base_model_prefix = "electra"
    model_config_file = "model_config.json"

    # pretrained general configuration
    gen_weight = 1.0
    disc_weight = 50.0
    tie_word_embeddings = True
    untied_generator_embeddings = False
    use_softmax_sample = True

    # model init configuration
    pretrained_init_configuration = {
        "electra-small": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 128,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 256,
            "initializer_range": 0.02,
            "intermediate_size": 1024,
            "max_position_embeddings": 512,
            "num_attention_heads": 4,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "electra-base": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 768,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 768,
            "initializer_range": 0.02,
            "intermediate_size": 3072,
            "max_position_embeddings": 512,
            "num_attention_heads": 12,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "electra-large": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 1024,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 1024,
            "initializer_range": 0.02,
            "intermediate_size": 4096,
            "max_position_embeddings": 512,
            "num_attention_heads": 16,
            "num_hidden_layers": 24,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "chinese-electra-small": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 128,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 256,
            "initializer_range": 0.02,
            "intermediate_size": 1024,
            "max_position_embeddings": 512,
            "num_attention_heads": 4,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 21128,
        },
        "chinese-electra-base": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 768,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 768,
            "initializer_range": 0.02,
            "intermediate_size": 3072,
            "max_position_embeddings": 512,
            "num_attention_heads": 12,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 21128,
        },
    }
    resource_files_names = {"model_state": "model_state.pdparams"}
    pretrained_resource_files_map = {
        "model_state": {
            "electra-small":
            "http://paddlenlp.bj.bcebos.com/models/transformers/electra/electra-small.pdparams",
            "electra-base":
            "http://paddlenlp.bj.bcebos.com/models/transformers/electra/electra-base.pdparams",
            "electra-large":
            "http://paddlenlp.bj.bcebos.com/models/transformers/electra/electra-large.pdparams",
            "chinese-electra-small":
            "http://paddlenlp.bj.bcebos.com/models/transformers/chinese-electra-small/chinese-electra-small.pdparams",
            "chinese-electra-base":
            "http://paddlenlp.bj.bcebos.com/models/transformers/chinese-electra-base/chinese-electra-base.pdparams",
        }
    }

    def init_weights(self):
        """
        Initializes and tie weights if needed.
        """
        # Initialize weights
        self.apply(self._init_weights)
        # Tie weights if needed
        self.tie_weights()

    def tie_weights(self):
        """
        Tie the weights between the input embeddings and the output embeddings.
        """
        if hasattr(self, "get_output_embeddings") and hasattr(
                self, "get_input_embeddings"):
            output_embeddings = self.get_output_embeddings()
            if output_embeddings is not None:
                self._tie_or_clone_weights(output_embeddings,
                                           self.get_input_embeddings())

    def _init_weights(self, layer):
        """ Initialize the weights """
        if isinstance(layer, (nn.Linear, nn.Embedding)):
            layer.weight.set_value(
                paddle.tensor.normal(
                    mean=0.0,
                    std=self.initializer_range
                    if hasattr(self, "initializer_range") else
                    self.electra.config["initializer_range"],
                    shape=layer.weight.shape))
        elif isinstance(layer, nn.LayerNorm):
            layer.bias.set_value(paddle.zeros_like(layer.bias))
            layer.weight.set_value(paddle.full_like(layer.weight, 1.0))
            layer._epsilon = 1e-12
        if isinstance(layer, nn.Linear) and layer.bias is not None:
            layer.bias.set_value(paddle.zeros_like(layer.bias))

    def _tie_or_clone_weights(self, output_embeddings, input_embeddings):
        """Tie or clone layer weights"""
        if output_embeddings.weight.shape == input_embeddings.weight.shape:
            output_embeddings.weight = input_embeddings.weight
        elif output_embeddings.weight.shape == input_embeddings.weight.t(
        ).shape:
            output_embeddings.weight.set_value(input_embeddings.weight.t())
        else:
            raise ValueError(
                "when tie input/output embeddings, the shape of output embeddings: {}"
                "should be equal to shape of input embeddings: {}"
                "or should be equal to the shape of transpose input embeddings: {}".
                format(output_embeddings.weight.shape, input_embeddings.weight.
                       shape, input_embeddings.weight.t().shape))
        if getattr(output_embeddings, "bias", None) is not None:
            if output_embeddings.weight.shape[
                    -1] != output_embeddings.bias.shape[0]:
                raise ValueError(
                    "the weight lase shape: {} of output_embeddings is not equal to the bias shape: {}"
                    "please check output_embeddings configuration".format(
                        output_embeddings.weight.shape[
                            -1], output_embeddings.bias.shape[0]))


@register_base_model
class ElectraModel(ElectraPretrainedModel):
    """
    The bare Electra Model transformer outputting raw hidden-states without any specific head on top.

    This model inherits from :class:`~paddlenlp.transformers.model_utils.PretrainedModel`.
    Check the superclass documentation for the generic methods and the library implements for all its model.

    This model is also a Paddle `paddle.nn.Layer <https://www.paddlepaddle.org.cn/documentation
    /docs/en/api/paddle/fluid/dygraph/layers/Layer_en.html>`__ subclass. Use it as a regular Paddle Layer
    and refer to the Paddle documentation for all matter related to general usage and behavior.

    Args:
        vocab_size (int):
            Vocabulary size of `inputs_ids` in `ElectraModel`. Defines the number of different tokens that can
            be represented by the `inputs_ids` passed when calling `ElectraModel`.
        hidden_size (int):
            Dimensionality of the encoder layers and the pooler layer.
        num_hidden_layers (int):
            Number of hidden layers in a hidden group in the Transformer encoder.
        num_attention_heads (int):
            Number of attention heads for each attention layer in the Transformer encoder.
        intermediate_size (int):
            Dimensionality of the "intermediate" (often named feed-forward) layer in the Transformer encoder.
        hidden_act (str):
            The non-linear activation function in the feed-forward layer.
            ``"gelu"``, ``"relu"`` and any other paddle supported activation functions
            are supported.
        hidden_dropout_prob (float):
            The dropout probability for all fully connected layers in the embeddings and encoder.
        attention_probs_dropout_prob (float):
            The dropout probability for all fully connected layers in the pooler.
        max_position_embeddings (int):
            The maximum value of the dimensionality of position encoding. The dimensionality of position encoding
            is the dimensionality of the sequence in `ElectraModel`.
        type_vocab_size (int):
            The vocabulary size of `token_type_ids` passed when calling `~ transformers.BertModel`.
            `token_type_ids` are segment token indices to indicate first
             and second portions of the inputs. Indices can either be 0 or 1:

                - 0 corresponds to a *sentence A* token,
                - 1 corresponds to a *sentence B* token.

        initializer_range (float):
            The standard deviation of the truncated_normal_initializer for initializing all weight matrices.

            .. note::
                A normal_initializer initializes weight matrices as normal distributions.
                See :meth:`ElectraPretrainedModel.init_weights()` for how weights are initialized in `ElectraModel`.

        pad_token_id (int, optional):
            The index of padding token in the token vocabulary.
            Defaults to `0`.

    """

    def __init__(self, vocab_size, embedding_size, hidden_size,
                 num_hidden_layers, num_attention_heads, intermediate_size,
                 hidden_act, hidden_dropout_prob, attention_probs_dropout_prob,
                 max_position_embeddings, type_vocab_size, initializer_range,
                 pad_token_id):
        super(ElectraModel, self).__init__()
        self.pad_token_id = pad_token_id
        self.initializer_range = initializer_range
        self.embeddings = ElectraEmbeddings(
            vocab_size, embedding_size, hidden_dropout_prob,
            max_position_embeddings, type_vocab_size)

        if embedding_size != hidden_size:
            self.embeddings_project = nn.Linear(embedding_size, hidden_size)

        encoder_layer = nn.TransformerEncoderLayer(
            hidden_size,
            num_attention_heads,
            intermediate_size,
            dropout=hidden_dropout_prob,
            activation=hidden_act,
            attn_dropout=attention_probs_dropout_prob,
            act_dropout=0)
        self.encoder = nn.TransformerEncoder(encoder_layer, num_hidden_layers)

        self.init_weights()

    def get_input_embeddings(self):
        return self.embeddings.word_embeddings

    def set_input_embeddings(self, value):
        self.embeddings.word_embeddings = value

    def forward(self,
                input_ids,
                token_type_ids=None,
                position_ids=None,
                attention_mask=None):
        r'''
        The ElectraModel forward method, overrides the `__call__()` special method.

        Args:
            input_ids (Tensor):
                Indices of input sequence tokens in the vocabulary. They are
                numerical representations of tokens that build the input sequence.
                Its data type should be `int64` and it has a shape of [batch_size, sequence_length].
            token_type_ids (Tensor, optional):
                Segment token indices to indicate first and second portions of the inputs.
                Indices can either be 0 or 1:

                - 0 corresponds to a *sentence A* token,
                - 1 corresponds to a *sentence B* token.

                Its data type should be `int64` and it has a shape of [batch_size, sequence_length].
                Defaults to `None`, which means we don't add segment embeddings.
            position_ids(Tensor, optional):
                Indices of positions of each input sequence tokens in the position embeddings. Selected in the range ``[0,
                config.max_position_embeddings - 1]``.
                Defaults to `None`. Shape as `(batch_sie, num_tokens)` and dtype as `int32` or `int64`.
            attention_mask (Tensor, optional):
                Mask to indicate whether to perform attention on each input token or not.
                The values should be either 0 or 1. The attention scores will be set to **-infinity**
                for any positions in mask that are **0**, and will be **unchanged** for positions that
                are **1**.

                - **1** for tokens that **not masked**,
                - **0** for tokens that **masked**.

                It's data type should be 'float32' and has a shape of [batch_size, sequence_length].
                Defaults to 'None'.


        Returns:
            Tensor: encoder_output(Tensor):
                Sequence of output at hidden layers of the model. Its data type should be float32 and
                has a shape of [batch_size, sequence_length, hidden_size].

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers import ElectraModel, ElectraTokenizer

                tokenizer = ElectraTokenizer.from_pretrained('electra-small')
                model = ElectraModel.from_pretrained('electra-small')

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                output = model(**inputs)
        '''

        if attention_mask is None:
            attention_mask = paddle.unsqueeze(
                (input_ids == self.pad_token_id
                 ).astype(paddle.get_default_dtype()) * -1e9,
                axis=[1, 2])

        embedding_output = self.embeddings(
            input_ids=input_ids,
            position_ids=position_ids,
            token_type_ids=token_type_ids)

        if hasattr(self, "embeddings_project"):
            embedding_output = self.embeddings_project(embedding_output)

        encoder_outputs = self.encoder(embedding_output, attention_mask)

        return encoder_outputs


class ElectraDiscriminator(ElectraPretrainedModel):
    """
    Electra Model with a discriminator head on top

    Args:
        electra (:class:`ElectraModel`):
            An instance of ElectraModel.
    """

    def __init__(self, electra):
        super(ElectraDiscriminator, self).__init__()

        self.electra = electra
        self.discriminator_predictions = ElectraDiscriminatorPredictions(
            self.electra.config["hidden_size"],
            self.electra.config["hidden_act"])
        self.init_weights()

    def forward(self,
                input_ids,
                token_type_ids=None,
                position_ids=None,
                attention_mask=None):
        r"""
        The ElectraDiscriminator forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`ElectraModel`.
            token_type_ids (Tensor, optional):
                See :class:`ElectraModel`.
            position_ids (Tensor, optional):
                See :class:`ElectraModel`.
            attention_mask (Tensor, optional):
                See :class:`ElectraModel`.

        Returns:
            logits (Tensor):
                A Tensor of the discriminator_predictions logits.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers import ElectraDiscriminator
                from paddlenlp.transformers import ElectraTokenizer

                tokenizer = ElectraTokenizer.from_pretrained('electra-base')
                model = ElectraDiscriminator.from_pretrained('electra-base',num_classes=2)

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                logits = model(**inputs)

        """

        discriminator_sequence_output = self.electra(
            input_ids, token_type_ids, position_ids, attention_mask)

        logits = self.discriminator_predictions(discriminator_sequence_output)

        return logits


class ElectraGenerator(ElectraPretrainedModel):
    """
    Electra Model with a generator head on top (a linear layer on top of the pooled output).

    Args:
        electra (:class:`ElectraModel`):
            An instance of ElectraModel.
    """

    def __init__(self, electra):
        super(ElectraGenerator, self).__init__()

        self.electra = electra
        self.generator_predictions = ElectraGeneratorPredictions(
            self.electra.config["embedding_size"],
            self.electra.config["hidden_size"],
            self.electra.config["hidden_act"])

        if not self.tie_word_embeddings:
            self.generator_lm_head = nn.Linear(
                self.electra.config["embedding_size"],
                self.electra.config["vocab_size"])
        else:
            self.generator_lm_head_bias = paddle.fluid.layers.create_parameter(
                shape=[self.electra.config["vocab_size"]],
                dtype=paddle.get_default_dtype(),
                is_bias=True)
        self.init_weights()

    def get_input_embeddings(self):
        return self.electra.embeddings.word_embeddings

    def forward(self,
                input_ids=None,
                token_type_ids=None,
                position_ids=None,
                attention_mask=None):
        r"""
        The ElectraGenerator forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`ElectraModel`.
            token_type_ids (Tensor, optional):
                See :class:`ElectraModel`.
            position_ids (Tensor, optional):
                See :class:`ElectraModel`.
            attention_mask (Tensor, optional):
                See :class:`ElectraModel`.

        Returns:
            prediction_scores (Tensor):
                A Tensor of the generator logits.
                Shape as `(batch_size, seq_lens, vocab_size)`, seq_lens mean the number of tokens of the input sequence.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers import ElectraGenerator
                from paddlenlp.transformers import ElectraTokenizer

                tokenizer = ElectraTokenizer.from_pretrained('electra-base')
                model = ElectraGenerator.from_pretrained('electra-base',num_classes=2)

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                prediction_scores = model(**inputs)

        """


        generator_sequence_output = self.electra(input_ids, token_type_ids,
                                                 position_ids, attention_mask)

        prediction_scores = self.generator_predictions(
            generator_sequence_output)
        if not self.tie_word_embeddings:
            prediction_scores = self.generator_lm_head(prediction_scores)
        else:
            prediction_scores = paddle.add(paddle.matmul(
                prediction_scores,
                self.get_input_embeddings().weight,
                transpose_y=True),
                                           self.generator_lm_head_bias)

        return prediction_scores


# class ElectraClassificationHead and ElectraForSequenceClassification for fine-tuning
class ElectraClassificationHead(nn.Layer):
    """
    Head for sentence-level classification tasks.

    Args:
            hidden_size (int):
                See :class:`ElectraModel`.
            hidden_dropout_prob (int):
                See :class:`ElectraModel`.
            num_classes (int):
                See :class:`ElectraModel`.
    """

    def __init__(self, hidden_size, hidden_dropout_prob, num_classes):
        super(ElectraClassificationHead, self).__init__()
        self.dense = nn.Linear(hidden_size, hidden_size)
        self.dropout = nn.Dropout(hidden_dropout_prob)
        self.out_proj = nn.Linear(hidden_size, num_classes)
        self.act = get_activation("gelu")

    def forward(self, features, **kwargs):
        """

    Args:
            features (Tensor):
                Features of the classification model.
            **kwargs (list):
                unpacked dict arguments

        """

        x = features[:, 0, :]  # take <s> token (equiv. to [CLS])
        x = self.dropout(x)
        x = self.dense(x)
        x = self.act(x)  # Electra paper used gelu here
        x = self.dropout(x)
        x = self.out_proj(x)
        return x


class ElectraForSequenceClassification(ElectraPretrainedModel):
    """
    Electra Model with a sequence classification/regression head on top (a linear layer on top of the pooled output) e.g.
    for GLUE tasks.

    Args:
        electrabert (:class:`ElectraModel`):
            An instance of ElectraModel.
        num_classes (int, optional):
            The number of classes.
    """

    def __init__(self, electra, num_classes):
        super(ElectraForSequenceClassification, self).__init__()
        self.num_classes = num_classes
        self.electra = electra
        self.classifier = ElectraClassificationHead(
            self.electra.config["hidden_size"],
            self.electra.config["hidden_dropout_prob"], self.num_classes)

        self.init_weights()

    def forward(self,
                input_ids=None,
                token_type_ids=None,
                position_ids=None,
                attention_mask=None):
        r"""
        The ElectraForSequenceClassification forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`ElectraModel`.
            token_type_ids (Tensor, optional):
                See :class:`ElectraModel`.
            position_ids (Tensor, optional):
                See :class:`ElectraModel`.
            attention_mask (Tensor, optional):
                See :class:`ElectraModel`.

        Returns:
            logits (Tensor):
                A Tensor of the input text classification logits.
                Shape as `(batch_size, num_classes)` and dtype as `float`.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers import ElectraForSequenceClassification
                from paddlenlp.transformers import ElectraTokenizer

                tokenizer = ElectraTokenizer.from_pretrained('electra-base')
                model = ElectraForSequenceClassification.from_pretrained('electra-base',num_classes=2)

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)

                logits = outputs[0]
        """

        sequence_output = self.electra(input_ids, token_type_ids, position_ids,
                                       attention_mask)

        logits = self.classifier(sequence_output)

        return logits


class ElectraForTokenClassification(ElectraPretrainedModel):
    """
    Electra Model with a token classification head on top (a linear layer on top of the hidden-states output) e.g.
    for Named-Entity-Recognition (NER) tasks.

    Args:
        electrabert (:class:`ElectraModel`):
            An instance of ElectraModel.
        num_classes (int, optional):
            The number of classes.
    """

    def __init__(self, electra, num_classes):
        super(ElectraForTokenClassification, self).__init__()
        self.num_classes = num_classes
        self.electra = electra
        self.dropout = nn.Dropout(self.electra.config["hidden_dropout_prob"])
        self.classifier = nn.Linear(self.electra.config["hidden_size"],
                                    self.num_classes)
        self.init_weights()

    def forward(self,
                input_ids=None,
                token_type_ids=None,
                position_ids=None,
                attention_mask=None):
        r"""
        The ElectraForTokenClassification forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`ElectraModel`.
            token_type_ids (Tensor, optional):
                See :class:`ElectraModel`.
            position_ids (Tensor, optional):
                See :class:`ElectraModel`.
            attention_mask (Tensor, optional):
                See :class:`ElectraModel`.

        Returns:
            logits (Tensor):
                A Tensor of the input text classification logits, shape as `(batch_size, seq_lens, num_classes)`.
                seq_lens mean the number of tokens of the input sequence.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers import ElectraForTokenClassification
                from paddlenlp.transformers import ElectraTokenizer

                tokenizer = ElectraTokenizer.from_pretrained('electra-small')
                model = ElectraForTokenClassification.from_pretrained('electra-small',num_classes=2)

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)

                logits = outputs[0]
        """

        sequence_output = self.electra(input_ids, token_type_ids, position_ids,
                                       attention_mask)

        sequence_output = self.dropout(sequence_output)
        logits = self.classifier(sequence_output)

        return logits


class ElectraForTotalPretraining(ElectraPretrainedModel):
    pretrained_init_configuration = {
        "electra-small-generator": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 128,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 256,
            "initializer_range": 0.02,
            "intermediate_size": 1024,
            "max_position_embeddings": 512,
            "num_attention_heads": 4,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "electra-base-generator": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 768,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 256,
            "initializer_range": 0.02,
            "intermediate_size": 1024,
            "max_position_embeddings": 512,
            "num_attention_heads": 4,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "electra-large-generator": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 1024,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 256,
            "initializer_range": 0.02,
            "intermediate_size": 1024,
            "max_position_embeddings": 512,
            "num_attention_heads": 4,
            "num_hidden_layers": 24,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "electra-small-discriminator": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 128,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 256,
            "initializer_range": 0.02,
            "intermediate_size": 1024,
            "max_position_embeddings": 512,
            "num_attention_heads": 4,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "electra-base-discriminator": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 768,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 768,
            "initializer_range": 0.02,
            "intermediate_size": 3072,
            "max_position_embeddings": 512,
            "num_attention_heads": 12,
            "num_hidden_layers": 12,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
        "electra-large-discriminator": {
            "attention_probs_dropout_prob": 0.1,
            "embedding_size": 1024,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "hidden_size": 1024,
            "initializer_range": 0.02,
            "intermediate_size": 4096,
            "max_position_embeddings": 512,
            "num_attention_heads": 16,
            "num_hidden_layers": 24,
            "pad_token_id": 0,
            "type_vocab_size": 2,
            "vocab_size": 30522
        },
    }

    def __init__(self, generator, discriminator):
        super(ElectraForTotalPretraining, self).__init__()

        self.generator = generator
        self.discriminator = discriminator
        self.initializer_range = discriminator.electra.initializer_range
        self.init_weights()

    def get_input_embeddings(self):
        if not self.untied_generator_embeddings:
            return self.generator.electra.embeddings.word_embeddings
        else:
            return None

    def get_output_embeddings(self):
        if not self.untied_generator_embeddings:
            return self.discriminator.electra.embeddings.word_embeddings
        else:
            return None

    def get_discriminator_inputs(self, inputs, raw_inputs, gen_logits,
                                 gen_labels, use_softmax_sample):
        """Sample from the generator to create discriminator input."""
        # get generator token result
        sampled_tokens = (self.sample_from_softmax(gen_logits,
                                                   use_softmax_sample)).detach()
        sampled_tokids = paddle.argmax(sampled_tokens, axis=-1)
        # update token only at mask position
        # gen_labels : [B, L], L contains -100(unmasked) or token value(masked)
        # mask_positions : [B, L], L contains 0(unmasked) or 1(masked)
        umask_positions = paddle.zeros_like(gen_labels)
        mask_positions = paddle.ones_like(gen_labels)
        mask_positions = paddle.where(gen_labels == -100, umask_positions,
                                      mask_positions)
        updated_inputs = self.update_inputs(inputs, sampled_tokids,
                                            mask_positions)
        # use inputs and updated_input to get discriminator labels
        labels = mask_positions * (paddle.ones_like(inputs) - paddle.equal(
            updated_inputs, raw_inputs).astype("int32"))
        return updated_inputs, labels, sampled_tokids

    def sample_from_softmax(self, logits, use_softmax_sample=True):
        if use_softmax_sample:
            #uniform_noise = paddle.uniform(logits.shape, dtype="float32", min=0, max=1)
            uniform_noise = paddle.rand(
                logits.shape, dtype=paddle.get_default_dtype())
            gumbel_noise = -paddle.log(-paddle.log(uniform_noise + 1e-9) + 1e-9)
        else:
            gumbel_noise = paddle.zeros_like(logits)
        # softmax_sample equal to sampled_tokids.unsqueeze(-1)
        softmax_sample = paddle.argmax(
            F.softmax(logits + gumbel_noise), axis=-1)
        # one hot
        return F.one_hot(softmax_sample, logits.shape[-1])

    def update_inputs(self, sequence, updates, positions):
        shape = sequence.shape
        assert (len(shape) == 2), "the dimension of inputs should be [B, L]"
        B, L = shape
        N = positions.shape[1]
        assert (
            N == L), "the dimension of inputs and mask should be same as [B, L]"

        updated_sequence = ((
            (paddle.ones_like(sequence) - positions) * sequence) +
                            (positions * updates))

        return updated_sequence

    def forward(self,
                input_ids=None,
                token_type_ids=None,
                position_ids=None,
                attention_mask=None,
                raw_input_ids=None,
                gen_labels=None):
        r"""

        Args:
            input_ids (Tensor):
               See :class:`ElectraModel`.
            token_type_ids (Tensor, optional):
                See :class:`ElectraModel`.
            position_ids (Tensor, optional):
                See :class:`ElectraModel`.
            attention_mask (Tensor, optional):
                See :class:`ElectraModel`.

        Returns:
            Tuple: A tuple of shape `(gen_logits, disc_logits, disc_labels, attention_mask)`.

            With the fields:

            - `gen_logits`(Tensor): Logits of the generator.
            - `disc_logits`(Tensor): Logits of the discriminator.
            - `disc_labels`(Tensor): Labels of the discriminator.
            - `attention_mask`(Tensor): Mask to indicate whether to perform attention on each input token or not.

        """

        assert (
            gen_labels is not None
        ), "gen_labels should not be None, please check DataCollatorForLanguageModeling"

        gen_logits = self.generator(input_ids, token_type_ids, position_ids,
                                    attention_mask)

        disc_inputs, disc_labels, generator_predict_tokens = self.get_discriminator_inputs(
            input_ids, raw_input_ids, gen_logits, gen_labels,
            self.use_softmax_sample)

        disc_logits = self.discriminator(disc_inputs, token_type_ids,
                                         position_ids, attention_mask)

        if attention_mask is None:
            attention_mask = (
                input_ids != self.discriminator.electra.config["pad_token_id"])
        else:
            attention_mask = attention_mask.astype("bool")

        return gen_logits, disc_logits, disc_labels, attention_mask


class ElectraPretrainingCriterion(paddle.nn.Layer):
    """
    Electra Criterion for a pretraining task on top.

    Args:
        vocab_size (int):
            See :class:`ElectraModel`.
        gen_weight (Tensor):
            Weights of the electra generator.
        disc_weight (Tensor):
            Weights of the electra discriminator.

    """

    def __init__(self, vocab_size, gen_weight, disc_weight):
        super(ElectraPretrainingCriterion, self).__init__()

        self.vocab_size = vocab_size
        self.gen_weight = gen_weight
        self.disc_weight = disc_weight
        self.gen_loss_fct = nn.CrossEntropyLoss(reduction='none')
        self.disc_loss_fct = nn.BCEWithLogitsLoss(reduction='none')

    def forward(self, generator_prediction_scores,
                discriminator_prediction_scores, generator_labels,
                discriminator_labels, attention_mask):
        r"""
        The ElectraPretrainingCriterion forward method, overrides the __call__() special method.

        Args:
            generator_prediction_scores (Tensor):
                The logits of generator.
            discriminator_prediction_scores (Tensor):
                The logits of discriminator.
            generator_labels (Tensor):
                The labels of the generator.
            discriminator_labels (Tensor):
                The labels of the discriminator.
            attention_mask (Tensor or int):
                Mask to indicate whether to perform attention on each input token or not.

        Returns:
            Float: The pretraining loss.
        """

        # generator loss
        gen_loss = self.gen_loss_fct(
            paddle.reshape(generator_prediction_scores, [-1, self.vocab_size]),
            paddle.reshape(generator_labels, [-1]))
        # todo: we can remove 4 lines after when CrossEntropyLoss(reduction='mean') improved
        umask_positions = paddle.zeros_like(generator_labels).astype(
            paddle.get_default_dtype())
        mask_positions = paddle.ones_like(generator_labels).astype(
            paddle.get_default_dtype())
        mask_positions = paddle.where(generator_labels == -100, umask_positions,
                                      mask_positions)
        if mask_positions.sum() == 0:
            gen_loss = paddle.to_tensor([0.0])
        else:
            gen_loss = gen_loss.sum() / mask_positions.sum()

        # discriminator loss
        seq_length = discriminator_labels.shape[1]
        disc_loss = self.disc_loss_fct(
            paddle.reshape(discriminator_prediction_scores, [-1, seq_length]),
            discriminator_labels.astype(paddle.get_default_dtype()))
        if attention_mask is not None:
            umask_positions = paddle.ones_like(discriminator_labels).astype(
                paddle.get_default_dtype())
            mask_positions = paddle.zeros_like(discriminator_labels).astype(
                paddle.get_default_dtype())
            use_disc_loss = paddle.where(attention_mask, disc_loss,
                                         mask_positions)
            umask_positions = paddle.where(attention_mask, umask_positions,
                                           mask_positions)
            disc_loss = use_disc_loss.sum() / umask_positions.sum()
        else:
            total_positions = paddle.ones_like(discriminator_labels).astype(
                paddle.get_default_dtype())
            disc_loss = disc_loss.sum() / total_positions.sum()

        return self.gen_weight * gen_loss + self.disc_weight * disc_loss
