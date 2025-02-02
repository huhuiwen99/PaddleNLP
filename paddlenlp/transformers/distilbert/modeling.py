# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
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

import paddle
import paddle.nn as nn

from .. import PretrainedModel, register_base_model

__all__ = [
    'DistilBertModel',
    'DistilBertPretrainedModel',
    'DistilBertForSequenceClassification',
    'DistilBertForTokenClassification',
    'DistilBertForQuestionAnswering',
    'DistilBertForMaskedLM',
]


class BertEmbeddings(nn.Layer):
    """
    Includes embeddings from word, position and does not include
    token_type embeddings.
    """

    def __init__(self,
                 vocab_size,
                 hidden_size=768,
                 hidden_dropout_prob=0.0,
                 max_position_embeddings=512,
                 type_vocab_size=16):
        super(BertEmbeddings, self).__init__()
        self.word_embeddings = nn.Embedding(vocab_size, hidden_size)
        self.position_embeddings = nn.Embedding(max_position_embeddings,
                                                hidden_size)
        self.layer_norm = nn.LayerNorm(hidden_size)
        self.dropout = nn.Dropout(hidden_dropout_prob)

    def forward(self, input_ids, position_ids=None):
        if position_ids is None:
            ones = paddle.ones_like(input_ids, dtype="int64")
            seq_length = paddle.cumsum(ones, axis=-1)

            position_ids = seq_length - ones
            position_ids.stop_gradient = True

        input_embeddings = self.word_embeddings(input_ids)
        position_embeddings = self.position_embeddings(position_ids)
        embeddings = input_embeddings + position_embeddings
        embeddings = self.layer_norm(embeddings)
        embeddings = self.dropout(embeddings)
        return embeddings


class DistilBertPretrainedModel(PretrainedModel):
    """
    An abstract class for pretrained DistilBERT models. It provides DistilBERT related
    `model_config_file`, `resource_files_names`, `pretrained_resource_files_map`,
    `pretrained_init_configuration`, `base_model_prefix` for downloading and
    loading pretrained models. See `PretrainedModel` for more details.
    """

    model_config_file = "model_config.json"
    pretrained_init_configuration = {
        "distilbert-base-uncased": {
            "vocab_size": 30522,
            "hidden_size": 768,
            "num_hidden_layers": 6,
            "num_attention_heads": 12,
            "intermediate_size": 3072,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "attention_probs_dropout_prob": 0.1,
            "max_position_embeddings": 512,
            "type_vocab_size": 2,
            "initializer_range": 0.02,
            "pad_token_id": 0,
        },
        "distilbert-base-cased": {
            "vocab_size": 28996,
            "hidden_size": 768,
            "num_hidden_layers": 6,
            "num_attention_heads": 12,
            "intermediate_size": 3072,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "attention_probs_dropout_prob": 0.1,
            "max_position_embeddings": 512,
            "type_vocab_size": 2,
            "initializer_range": 0.02,
            "pad_token_id": 0,
        },
    }
    resource_files_names = {"model_state": "model_state.pdparams"}
    pretrained_resource_files_map = {
        "model_state": {
            "distilbert-base-uncased":
            "http://paddlenlp.bj.bcebos.com/models/transformers/distilbert/distilbert-base-uncased.pdparams",
            "distilbert-base-cased":
            "http://paddlenlp.bj.bcebos.com/models/transformers/distilbert/distilbert-base-cased.pdparams",
        }
    }
    base_model_prefix = "distilbert"

    def init_weights(self, layer):
        """ Initialization hook """
        if isinstance(layer, (nn.Linear, nn.Embedding)):
            # In the dygraph mode, use the `set_value` to reset the parameter directly,
            # and reset the `state_dict` to update parameter in static mode.
            if isinstance(layer.weight, paddle.Tensor):
                layer.weight.set_value(
                    paddle.tensor.normal(
                        mean=0.0,
                        std=self.initializer_range
                        if hasattr(self, "initializer_range") else
                        self.distilbert.config["initializer_range"],
                        shape=layer.weight.shape))
        elif isinstance(layer, nn.LayerNorm):
            layer._epsilon = 1e-12


@register_base_model
class DistilBertModel(DistilBertPretrainedModel):
    """
    The bare DistilBert Model transformer outputting raw hidden-states without any specific head on top.

    This model inherits from :class:`~paddlenlp.transformers.model_utils.PretrainedModel`.
    Check the superclass documentation for the generic methods and the library implements for all its model.

    This model is also a Paddle `paddle.nn.Layer <https://www.paddlepaddle.org.cn/documentation
    /docs/en/api/paddle/fluid/dygraph/layers/Layer_en.html>`__ subclass. Use it as a regular Paddle Layer
    and refer to the Paddle documentation for all matter related to general usage and behavior.

    Args:
        vocab_size (int):
            Vocabulary size of `inputs_ids` in `DistilBertModel`. Defines the number of different tokens that can
            be represented by the `inputs_ids` passed when calling `DistilBertModel`.
        hidden_size (int, optional):
            Dimensionality of the encoder layers and the pooler layer. Defaults to `768`.
        num_hidden_layers (int, optional):
            Number of hidden layers in the Transformer encoder. Defaults to `12`.
        num_attention_heads (int, optional):
            Number of attention heads for each attention layer in the Transformer encoder.
            Defaults to `12`.
        intermediate_size (int, optional):
            Dimensionality of the "intermediate" (often named feed-forward) layer in the Transformer encoder.
            Defaults to `3072`.
        hidden_act (str, optional):
            The non-linear activation function in the feed-forward layer.
            ``"gelu"``, ``"relu"`` and any other paddle supported activation functions
            are supported. Defaults to `"gelu"`.
        hidden_dropout_prob (float, optional):
            The dropout probability for all fully connected layers in the embeddings and encoder.
            Defaults to `0.1`.
        attention_probs_dropout_prob (float, optional):
            The dropout probability for all fully connected layers in the pooler.
            Defaults to `0.1`.
        max_position_embeddings (int, optional):
            The maximum value of the dimensionality of position encoding. The dimensionality of position encoding
            is the dimensionality of the sequence in `DistilBertModel`.
            Defaults to `512`.
        type_vocab_size (int, optional):
            The vocabulary size of `token_type_ids` passed when calling `~ transformers.DistilBertModel`.
            Defaults to `16`.
            `token_type_ids` are segment token indices to indicate first
             and second portions of the inputs. Indices can either be 0 or 1:

                - 0 corresponds to a *sentence A* token,
                - 1 corresponds to a *sentence B* token.

        initializer_range (float, optional):
            The standard deviation of the truncated_normal_initializer for initializing all weight matrices.
            Defaults to `0.02`.

            .. note::
                A normal_initializer initializes weight matrices as normal distributions.
                See :meth:`DistilBertPretrainedModel.init_weights()` for how weights are initialized in `DistilBertModel`.

        pad_token_id (int, optional):
            The index of padding token in the token vocabulary.
            Defaults to `0`.

    """

    def __init__(self,
                 vocab_size,
                 hidden_size=768,
                 num_hidden_layers=12,
                 num_attention_heads=12,
                 intermediate_size=3072,
                 hidden_act="gelu",
                 hidden_dropout_prob=0.1,
                 attention_probs_dropout_prob=0.1,
                 max_position_embeddings=512,
                 type_vocab_size=16,
                 initializer_range=0.02,
                 pad_token_id=0):
        super(DistilBertModel, self).__init__()
        self.pad_token_id = pad_token_id
        self.initializer_range = initializer_range
        self.embeddings = BertEmbeddings(
            vocab_size, hidden_size, hidden_dropout_prob,
            max_position_embeddings, type_vocab_size)
        encoder_layer = nn.TransformerEncoderLayer(
            hidden_size,
            num_attention_heads,
            intermediate_size,
            dropout=hidden_dropout_prob,
            activation=hidden_act,
            attn_dropout=attention_probs_dropout_prob,
            act_dropout=0)
        self.encoder = nn.TransformerEncoder(encoder_layer, num_hidden_layers)
        self.apply(self.init_weights)

    def forward(self, input_ids, attention_mask=None):
        r'''
        The DistilBertModel forward method, overrides the `__call__()` special method.

        Args:
            input_ids (Tensor):
                Indices of input sequence tokens in the vocabulary. They are
                numerical representations of tokens that build the input sequence.
                Its data type should be `int64` and it has a shape of [batch_size, sequence_length].
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
                from paddlenlp.transformers import DistilBertModel, DistilBertTokenizer

                tokenizer = DistilBertTokenizer.from_pretrained('distilbert-base-uncased')
                model = DistilBertModel.from_pretrained('distilbert-base-uncased')

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                output = model(**inputs)
        '''

        if attention_mask is None:
            attention_mask = paddle.unsqueeze(
                (input_ids == self.pad_token_id
                 ).astype(self.encoder.layers[0].norm1.weight.dtype) * -1e9,
                axis=[1, 2])
        embedding_output = self.embeddings(input_ids=input_ids)
        encoder_outputs = self.encoder(embedding_output, attention_mask)

        return encoder_outputs


class DistilBertForSequenceClassification(DistilBertPretrainedModel):
    """
    DistilBert Model with a sequence classification/regression head on top (a linear layer on top of the pooled output) e.g.
    for GLUE tasks.

    Args:
        distilbert (:class:`DistilBertModel`):
            An instance of DistilBertModel.
        num_classes (int, optional):
            The number of classes. Default `2`.
        dropout (float, optional):
            The dropout probability for output of DistilBert.
            If None, use the same value as `hidden_dropout_prob` of `DistilBertModel`
            instance `distilbert`. Default None.
    """

    def __init__(self, distilbert, num_classes=2, dropout=None):
        super(DistilBertForSequenceClassification, self).__init__()
        self.num_classes = num_classes
        self.distilbert = distilbert  # allow bert to be config
        self.pre_classifier = nn.Linear(self.distilbert.config["hidden_size"],
                                        self.distilbert.config["hidden_size"])
        self.activation = nn.ReLU()
        self.dropout = nn.Dropout(dropout if dropout is not None else
                                  self.distilbert.config["hidden_dropout_prob"])
        self.classifier = nn.Linear(self.distilbert.config["hidden_size"],
                                    num_classes)
        self.apply(self.init_weights)

    def forward(self, input_ids, attention_mask=None):
        r"""
        The DistilBertForSequenceClassification forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`DistilBertModel`.
            attention_mask_list (list, optional):
                See :class:`DistilBertModel`.

        Returns:
            logits (Tensor):
                A Tensor of the input text classification logits.
                Shape as `(batch_size, num_classes)` and dtype as `float`.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers.distilbert.modeling import DistilBertForSequenceClassification
                from paddlenlp.transformers.distilbert.tokenizer import DistilBertTokenizer

                tokenizer = DistilBertTokenizer.from_pretrained('distilbert-base-uncased')
                model = DistilBertForSequenceClassification.from_pretrained('distilbert-base-uncased')

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)

                logits = outputs[0]
        """

        distilbert_output = self.distilbert(
            input_ids=input_ids, attention_mask=attention_mask)

        pooled_output = distilbert_output[:, 0]
        pooled_output = self.pre_classifier(pooled_output)
        pooled_output = self.activation(pooled_output)

        pooled_output = self.dropout(pooled_output)
        logits = self.classifier(pooled_output)

        return logits


class DistilBertForQuestionAnswering(DistilBertPretrainedModel):
    """
    DistilBert Model with a span classification head on top for extractive question-answering tasks like
    SQuAD (a linear layers on top of the hidden-states output to compute `span start logits` and
    `span end logits`).

    Args:
        distilbert (:class:`DistilBertModel`):
            An instance of DistilBertModel.
        dropout (float, optional):
            The dropout probability for output of DistilBert.
            If None, use the same value as `hidden_dropout_prob` of `DistilBertModel`
            instance `distilbert`. Default None.
    """

    def __init__(self, distilbert, dropout=None):
        super(DistilBertForQuestionAnswering, self).__init__()
        self.distilbert = distilbert  # allow bert to be config
        self.dropout = nn.Dropout(dropout if dropout is not None else
                                  self.distilbert.config["hidden_dropout_prob"])
        self.classifier = nn.Linear(self.distilbert.config["hidden_size"], 2)
        self.apply(self.init_weights)

    def forward(self, input_ids, attention_mask=None):
        r"""
        The DistilBertForQuestionAnswering forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`DistilBertModel`.
            attention_mask_list (list, optional):
                See :class:`DistilBertModel`.

        Returns:
            A tuple of shape (`start_logits`, `end_logits`).

            With the fields:

            - start_logits(Tensor): Labels for position (index) of the start of the labelled span for computing the token classification loss.
            Positions are clamped to the length of the sequence (:obj:`sequence_length`). Position outside of the
            sequence are not taken into account for computing the loss.

            - end_logits(Tensor): Labels for position (index) of the end of the labelled span for computing the token classification loss.
            Positions are clamped to the length of the sequence (:obj:`sequence_length`). Position outside of the
            sequence are not taken into account for computing the loss.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers.distilbert.modeling import DistilBertForQuestionAnswering
                from paddlenlp.transformers.distilbert.tokenizer import DistilBertTokenizer

                tokenizer = DistilBertTokenizer.from_pretrained('distilbert-base-uncased')
                model = DistilBertForQuestionAnswering.from_pretrained('distilbert-base-uncased')

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)

                start_logits = outputs[0]
                end_logits  =outputs[1]
        """

        sequence_output = self.distilbert(
            input_ids, attention_mask=attention_mask)
        sequence_output = self.dropout(sequence_output)
        logits = self.classifier(sequence_output)
        logits = paddle.transpose(logits, perm=[2, 0, 1])
        start_logits, end_logits = paddle.unstack(x=logits, axis=0)
        return start_logits, end_logits


class DistilBertForTokenClassification(DistilBertPretrainedModel):
    """
    DistilBert Model with a token classification head on top (a linear layer on top of the hidden-states output) e.g.
    for Named-Entity-Recognition (NER) tasks.

    Args:
        distilbert (:class:`DistilBertModel`):
            An instance of DistilBertModel.
        num_classes (int, optional):
            The number of classes. Default `2`.
        dropout (float, optional):
            The dropout probability for output of DistilBert.
            If None, use the same value as `hidden_dropout_prob` of `DistilBertModel`
            instance `distilbert`. Default None.
    """

    def __init__(self, distilbert, num_classes=2, dropout=None):
        super(DistilBertForTokenClassification, self).__init__()
        self.num_classes = num_classes
        self.distilbert = distilbert  # allow bert to be config
        self.dropout = nn.Dropout(dropout if dropout is not None else
                                  self.distilbert.config["hidden_dropout_prob"])
        self.classifier = nn.Linear(self.distilbert.config["hidden_size"],
                                    num_classes)
        self.apply(self.init_weights)

    def forward(self, input_ids, attention_mask=None):
        r"""
        The DistilBertForTokenClassification forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`DistilBertModel`.
            attention_mask_list (list, optional):
                See :class:`DistilBertModel`.

        Returns:
            logits (Tensor):
                A Tensor of the input text classification logits, shape as `(batch_size, seq_lens, num_classes)`.
                seq_lens mean the number of tokens of the input sequence.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers.distilbert.modeling import DistilBertForTokenClassification
                from paddlenlp.transformers.distilbert.tokenizer import DistilBertTokenizer

                tokenizer = DistilBertTokenizer.from_pretrained('distilbert-base-uncased')
                model = DistilBertForTokenClassification.from_pretrained('distilbert-base-uncased')

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)

                logits = outputs[0]
        """

        sequence_output = self.distilbert(
            input_ids, attention_mask=attention_mask)

        sequence_output = self.dropout(sequence_output)
        logits = self.classifier(sequence_output)
        return logits


class DistilBertForMaskedLM(DistilBertPretrainedModel):
    """
    DistilBert Model with a `language modeling` head on top.

    Args:
        distilbert (:class:`DistilBertModel`):
            An instance of DistilBertModel.
    """

    def __init__(self, distilbert):
        super(DistilBertForMaskedLM, self).__init__()
        self.distilbert = distilbert
        self.vocab_transform = nn.Linear(self.distilbert.config["hidden_size"],
                                         self.distilbert.config["hidden_size"])
        self.activation = nn.GELU()
        self.vocab_layer_norm = nn.LayerNorm(self.distilbert.config[
            "hidden_size"])
        self.vocab_projector = nn.Linear(self.distilbert.config["hidden_size"],
                                         self.distilbert.config["vocab_size"])

        self.apply(self.init_weights)

    def forward(self, input_ids=None, attention_mask=None):
        r'''
        The DistilBertModel forward method, overrides the `__call__()` special method.

        Args:
            input_ids (Tensor):
                See :class:`DistilBertModel`.
            attention_mask (Tensor, optional):
                See :class:`DistilBertModel`.

        Returns:
            prediction_logits (Tensor): The scores of prediction on masked token.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers import DistilBertForMaskedLM, AlbertTokenizer

                tokenizer = AlbertTokenizer.from_pretrained('distilbert-base-uncased')
                model = DistilBertForMaskedLM.from_pretrained('distilbert-base-uncased')

                inputs = tokenizer("This is a test example.")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                prediction_logits = model(**inputs)
        '''

        distilbert_output = self.distilbert(
            input_ids=input_ids, attention_mask=attention_mask)
        prediction_logits = self.vocab_transform(distilbert_output)
        prediction_logits = self.activation(prediction_logits)
        prediction_logits = self.vocab_layer_norm(prediction_logits)
        prediction_logits = self.vocab_projector(prediction_logits)
        return prediction_logits
