import copy
import math
import numpy as np

import paddle
import paddle.nn as nn
import paddle.nn.functional as F

from paddlenlp.transformers import PretrainedModel, register_base_model


__all__ = [
    'NeZhaModel',
    "NeZhaPretrainedModel",
    'NeZhaForPretraining',
    'NeZhaForSequenceClassification',
    'NeZhaPretrainingHeads',
    'NeZhaForTokenClassification',
    'NeZhaForQuestionAnswering',
    'NeZhaForMultipleChoice'
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


def gelu_new(x):
    """
    Implementation of the GELU activation function currently in Google BERT repo (identical to OpenAI GPT). Also see
    the Gaussian Error Linear Units paper: https://arxiv.org/abs/1606.08415
    """
    return 0.5 * x * (1.0 + paddle.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * paddle.pow(x, 3.0))))


ACT2FN = {
    "relu": F.relu,
    "gelu": F.gelu,
    "gelu_new": gelu_new,
    "tanh": F.tanh,
    "sigmoid": F.sigmoid,
    "mish": mish,
    "linear": linear_act,
    "swish": swish,
}


class NeZhaAttention(nn.Layer):
    def __init__(self,
                 hidden_size,
                 num_attention_heads,
                 hidden_dropout_prob,
                 attention_probs_dropout_prob,
                 max_relative_position,
                 layer_norm_eps):
        super(NeZhaAttention, self).__init__()
        if hidden_size % num_attention_heads != 0:
            raise ValueError(
                "The hidden size (%d) is not a multiple of the number of attention "
                "heads (%d)" % (hidden_size, num_attention_heads))
        self.num_attention_heads = num_attention_heads
        self.attention_head_size = int(hidden_size / num_attention_heads)
        self.all_head_size = self.num_attention_heads * self.attention_head_size

        self.query = nn.Linear(hidden_size, self.all_head_size)
        self.key = nn.Linear(hidden_size, self.all_head_size)
        self.value = nn.Linear(hidden_size, self.all_head_size)
        self.relative_positions_embeddings = self.generate_relative_positions_embeddings(
            length=512, depth=self.attention_head_size, max_relative_position=max_relative_position
        )
        self.attention_dropout = nn.Dropout(attention_probs_dropout_prob)

        self.dense = nn.Linear(hidden_size, hidden_size)
        self.layer_norm = nn.LayerNorm(hidden_size, epsilon=layer_norm_eps)
        self.output_dropout = nn.Dropout(hidden_dropout_prob)

    def generate_relative_positions_embeddings(self, length, depth, max_relative_position=127):
        vocab_size = max_relative_position * 2 + 1
        range_vec = paddle.arange(length)
        range_mat = paddle.tile(
            range_vec, repeat_times=[length]
        ).reshape((length, length))
        distance_mat = range_mat - paddle.t(range_mat)
        distance_mat_clipped = paddle.clip(
            distance_mat.astype( 'float32'), 
            -max_relative_position, 
            max_relative_position
        )
        final_mat = distance_mat_clipped + max_relative_position
        embeddings_table = np.zeros([vocab_size, depth])

        for pos in range(vocab_size):
            for i in range(depth // 2):
                embeddings_table[pos, 2 * i] = np.sin(pos / np.power(10000, 2 * i / depth))
                embeddings_table[pos, 2 * i + 1] = np.cos(pos / np.power(10000, 2 * i / depth))

        embeddings_table_tensor = paddle.to_tensor(embeddings_table, dtype='float32')
        flat_relative_positions_matrix = final_mat.reshape((-1,))
        one_hot_relative_positions_matrix = paddle.nn.functional.one_hot(
            flat_relative_positions_matrix.astype('int64'), 
            num_classes=vocab_size
        )
        embeddings = paddle.matmul(
            one_hot_relative_positions_matrix, 
            embeddings_table_tensor
        )
        my_shape = final_mat.shape
        my_shape.append(depth)
        embeddings = embeddings.reshape(my_shape)
        return embeddings

    def transpose_for_scores(self, x):
        new_x_shape = x.shape[:-1] + [self.num_attention_heads, self.attention_head_size]
        x = x.reshape(new_x_shape)
        return x.transpose((0, 2, 1, 3))

    def forward(self, hidden_states, attention_mask):
        mixed_query_layer = self.query(hidden_states)
        mixed_key_layer = self.key(hidden_states)
        mixed_value_layer = self.value(hidden_states)

        query_layer = self.transpose_for_scores(mixed_query_layer)
        key_layer = self.transpose_for_scores(mixed_key_layer)
        value_layer = self.transpose_for_scores(mixed_value_layer)

        # Take the dot product between "query" and "key" to get the raw attention scores.
        attention_scores = paddle.matmul(
            query_layer, 
            key_layer.transpose((0, 1, 3, 2))
        )
        batch_size, num_attention_heads, from_seq_length, to_seq_length = attention_scores.shape

        relations_keys = self.relative_positions_embeddings.detach().clone()[:to_seq_length, :to_seq_length, :]

        query_layer_t = query_layer.transpose((2, 0, 1, 3))
        query_layer_r = query_layer_t.reshape(
            (from_seq_length, batch_size *
             num_attention_heads, self.attention_head_size)
        )
        key_position_scores = paddle.matmul(
            query_layer_r, 
            relations_keys.transpose((0, 2, 1))
        )
        key_position_scores_r = key_position_scores.reshape(
            (from_seq_length, batch_size, num_attention_heads, from_seq_length)
        )
        key_position_scores_r_t = key_position_scores_r.transpose((1, 2, 0, 3))
        attention_scores = attention_scores + key_position_scores_r_t
        attention_scores = attention_scores / math.sqrt(self.attention_head_size)
        attention_scores = attention_scores + attention_mask

        # Normalize the attention scores to probabilities.
        attention_probs = nn.Softmax(axis=-1)(attention_scores)

        # This is actually dropping out entire tokens to attend to, which might
        # seem a bit unusual, but is taken from the original Transformer paper.
        attention_probs = self.attention_dropout(attention_probs)

        context_layer = paddle.matmul(attention_probs, value_layer)

        relations_values = self.relative_positions_embeddings.clone()[:to_seq_length, :to_seq_length, :]
        attention_probs_t = attention_probs.transpose((2, 0, 1, 3))
        attentions_probs_r = attention_probs_t.reshape(
            (from_seq_length, batch_size * num_attention_heads, to_seq_length)
        )
        value_position_scores = paddle.matmul(attentions_probs_r, relations_values)
        value_position_scores_r = value_position_scores.reshape(
            (from_seq_length, batch_size,
             num_attention_heads, self.attention_head_size)
        )
        value_position_scores_r_t = value_position_scores_r.transpose((1, 2, 0, 3))
        context_layer = context_layer + value_position_scores_r_t

        context_layer = context_layer.transpose((0, 2, 1, 3))
        new_context_layer_shape = context_layer.shape[:-2] + [self.all_head_size]
        context_layer = context_layer.reshape(new_context_layer_shape)

        projected_context_layer = self.dense(context_layer)
        projected_context_layer_dropout = self.output_dropout(projected_context_layer)
        layer_normed_context_layer = self.layer_norm(
            hidden_states + projected_context_layer_dropout
        )

        return layer_normed_context_layer, attention_scores


class NeZhaLayer(nn.Layer):
    def __init__(self,
                 hidden_size,
                 num_attention_heads,
                 intermediate_size,
                 hidden_act,
                 hidden_dropout_prob,
                 attention_probs_dropout_prob,
                 max_relative_position,
                 layer_norm_eps):
        super(NeZhaLayer, self).__init__()
        self.seq_len_dim = 1
        self.layer_norm = nn.LayerNorm(hidden_size, epsilon=layer_norm_eps)
        self.attention = NeZhaAttention(
            hidden_size,
            num_attention_heads,
            hidden_dropout_prob,
            attention_probs_dropout_prob,
            max_relative_position,
            layer_norm_eps
        )
        self.ffn = nn.Linear(hidden_size, intermediate_size)
        self.ffn_output = nn.Linear(intermediate_size, hidden_size)
        self.activation = ACT2FN[hidden_act]
        self.dropout = nn.Dropout(hidden_dropout_prob)

    def forward(self, hidden_states, attention_mask=None):
        attention_output, layer_att = self.attention(hidden_states, attention_mask)

        ffn_output = self.ffn(attention_output)
        ffn_output = self.activation(ffn_output)
        ffn_output = self.ffn_output(ffn_output)

        ffn_output_dropout = self.dropout(ffn_output)
        hidden_states = self.layer_norm(ffn_output_dropout + attention_output)

        return hidden_states, layer_att


class NeZhaEncoder(nn.Layer):
    def __init__(self,
                 hidden_size,
                 num_hidden_layers,
                 num_attention_heads,
                 intermediate_size,
                 hidden_act,
                 hidden_dropout_prob,
                 attention_probs_dropout_prob,
                 max_relative_position,
                 layer_norm_eps):
        super(NeZhaEncoder, self).__init__()
        layer = NeZhaLayer(
            hidden_size,
            num_attention_heads,
            intermediate_size,
            hidden_act,
            hidden_dropout_prob,
            attention_probs_dropout_prob,
            max_relative_position,
            layer_norm_eps
        )
        self.layer = nn.LayerList([copy.deepcopy(layer) for _ in range(num_hidden_layers)])

    def forward(self, hidden_states, attention_mask):
        all_encoder_layers = []
        all_encoder_att = []
        for i, layer_module in enumerate(self.layer):
            all_encoder_layers.append(hidden_states)
            hidden_states, layer_att = layer_module(all_encoder_layers[i], attention_mask)
            all_encoder_att.append(layer_att)
        all_encoder_layers.append(hidden_states)
        return all_encoder_layers, all_encoder_att


class NeZhaEmbeddings(nn.Layer):
    def __init__(self,
                 vocab_size,
                 hidden_size=768,
                 hidden_dropout_prob=0.1,
                 max_position_embeddings=512,
                 type_vocab_size=16,
                 use_relative_position=True):
        super(NeZhaEmbeddings, self).__init__()
        self.use_relative_position = use_relative_position

        self.word_embeddings = nn.Embedding(vocab_size, hidden_size)

        if not use_relative_position:
            self.position_embeddings = nn.Embedding(
                max_position_embeddings, hidden_size)

        self.token_type_embeddings = nn.Embedding(type_vocab_size, hidden_size)
        self.layer_norm = nn.LayerNorm(hidden_size)
        self.dropout = nn.Dropout(hidden_dropout_prob)

    def forward(self, input_ids, token_type_ids=None):
        seq_length = input_ids.shape[1]
        position_ids = paddle.arange(seq_length, dtype='int64')
        position_ids = position_ids.unsqueeze(0).expand_as(input_ids)

        if token_type_ids is None:
            token_type_ids = paddle.zeros_like(input_ids, dtype="int64")

        words_embeddings = self.word_embeddings(input_ids)
        embeddings = words_embeddings

        if not self.use_relative_position:
            position_embeddings = self.position_embeddings(position_ids)
            embeddings += position_embeddings

        token_type_embeddings = self.token_type_embeddings(token_type_ids)

        embeddings += token_type_embeddings
        embeddings = self.layer_norm(embeddings)
        embeddings = self.dropout(embeddings)

        return embeddings


class NeZhaPooler(nn.Layer):
    def __init__(self, hidden_size):
        super(NeZhaPooler, self).__init__()
        self.dense = nn.Linear(hidden_size, hidden_size)
        self.activation = nn.Tanh()

    def forward(self, hidden_states):
        # We "pool" the model by simply taking the hidden state corresponding
        # to the first token.
        first_token_tensor = hidden_states[:, 0]
        pooled_output = self.dense(first_token_tensor)
        pooled_output = self.activation(pooled_output)
        return pooled_output


class NeZhaPretrainedModel(PretrainedModel):
    """
    An abstract class for pretrained NeZha models. It provides NeZha related
    `model_config_file`, `resource_files_names`, `pretrained_resource_files_map`,
    `pretrained_init_configuration`, `base_model_prefix` for downloading and
    loading pretrained models. See `PretrainedModel` for more details.
    """

    model_config_file = "model_config.json"
    pretrained_init_configuration = {
        "nezha-base-chinese": {
            "vocab_size": 21128,
            "hidden_size": 768,
            "num_hidden_layers": 12,
            "num_attention_heads": 12,
            "intermediate_size": 3072,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "attention_probs_dropout_prob": 0.1,
            "max_position_embeddings": 512,
            "max_relative_position": 64,
            "type_vocab_size": 2,
            "initializer_range": 0.02,
            "use_relative_position": True
        },
        "nezha-large-chinese": {
            "vocab_size": 21128,
            "hidden_size": 1024,
            "num_hidden_layers": 24,
            "num_attention_heads": 16,
            "intermediate_size": 4096,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "attention_probs_dropout_prob": 0.1,
            "max_position_embeddings": 512,
            "max_relative_position": 64,
            "type_vocab_size": 2,
            "initializer_range": 0.02,
            "use_relative_position": True
        },
        "nezha-base-wwm-chinese": {
            "vocab_size": 21128,
            "hidden_size": 768,
            "num_hidden_layers": 12,
            "num_attention_heads": 12,
            "intermediate_size": 3072,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "attention_probs_dropout_prob": 0.1,
            "max_position_embeddings": 512,
            "max_relative_position": 64,
            "type_vocab_size": 2,
            "initializer_range": 0.02,
            "use_relative_position": True
        },
        "nezha-large-wwm-chinese": {
            "vocab_size": 21128,
            "hidden_size": 1024,
            "num_hidden_layers": 24,
            "num_attention_heads": 16,
            "intermediate_size": 4096,
            "hidden_act": "gelu",
            "hidden_dropout_prob": 0.1,
            "attention_probs_dropout_prob": 0.1,
            "max_position_embeddings": 512,
            "max_relative_position": 64,
            "type_vocab_size": 2,
            "initializer_range": 0.02,
            "use_relative_position": True
        },
    }
    resource_files_names = {"model_state": "model_state.pdparams"}
    pretrained_resource_files_map = {
        "model_state": {
            "nezha-base-chinese":
            "https://paddlenlp.bj.bcebos.com/models/transformers/nezha/nezha-base-chinese.pdparams",
            "nezha-large-chinese":
            "https://paddlenlp.bj.bcebos.com/models/transformers/nezha/nezha-large-chinese.pdparams",
            "nezha-base-wwm-chinese":
            "https://paddlenlp.bj.bcebos.com/models/transformers/nezha/nezha-base-wwm-chinese.pdparams",
            "nezha-large-wwm-chinese":
            "https://paddlenlp.bj.bcebos.com/models/transformers/nezha/nezha-large-wwm-chinese.pdparams",
        }
    }
    base_model_prefix = "nezha"

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
                        self.nezha.config["initializer_range"],
                        shape=layer.weight.shape))
        elif isinstance(layer, nn.LayerNorm):
            layer._epsilon = 1e-12


@register_base_model
class NeZhaModel(NeZhaPretrainedModel):
    """
    The bare NeZha Model transformer outputting raw hidden-states without any specific head on top.

    This model inherits from :class:`~paddlenlp.transformers.model_utils.PretrainedModel`.
    Check the superclass documentation for the generic methods and the library implements for all its model.

    This model is also a Paddle `paddle.nn.Layer <https://www.paddlepaddle.org.cn/documentation
    /docs/en/api/paddle/fluid/dygraph/layers/Layer_en.html>`__ subclass. Use it as a regular Paddle Layer
    and refer to the Paddle documentation for all matter related to general usage and behavior.

    Args:
        vocab_size (int):
            Vocabulary size of `inputs_ids` in `NeZhaModel`.Defines the number of different tokens that can
            be represented by the `inputs_ids` passed when calling `NeZhaModel`.
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
            are supported. Defaults to "gelu".
        hidden_dropout_prob (float, optional):
            The dropout probability for all fully connected layers in the embeddings and encoder.
            Defaults to `0.1`.
        attention_probs_dropout_prob (float, optional):
            The dropout probability for all fully connected layers in the pooler.
            Defaults to `0.1`.
        max_position_embeddings (int, optional):
            The maximum value of the dimensionality of position encoding. The dimensionality of position encoding
            is the dimensionality of the sequence in `NeZhaModel`.
            Defaults to `512`.
        type_vocab_size (int, optional):
            The vocabulary size of `token_type_ids` passed when calling `~ transformers.NeZhaModel`.
            Defaults to `12`.
            `token_type_ids` are segment token indices to indicate first
             and second portions of the inputs. Indices can either be 0 or 1:

                - 0 corresponds to a *sentence A* token,
                - 1 corresponds to a *sentence B* token.

        initializer_range (float, optional):
            The standard deviation of the truncated_normal_initializer for initializing all weight matrices.
            Defaults to `0.02`.

            .. note::
                A normal_initializer initializes weight matrices as normal distributions.
                See :meth:`NeZhaPretrainedModel.init_weights()` for how weights are initialized in `NeZhaModel`.

        max_relative_positions(int, optional):
            The maximum value of relative position.
            Defaults to `64`.
        layer_norm_eps(float, optional):
            The `epsilon` parameter used in :class:`paddle.nn.LayerNorm` for initializing layer normalization layers.
            A small value to the variance added to the normalization layer to prevent division by zero.
            Defaults to `1e-12`.
        use_relative_position(bool, optional):
            Whether to use relative position in position embedding. Defaults to `False`.

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
                 type_vocab_size=2,
                 initializer_range=0.02,
                 max_relative_position=64,
                 layer_norm_eps=1e-12,
                 use_relative_position=True):
        super(NeZhaModel, self).__init__()
        self.initializer_range = initializer_range

        self.embeddings = NeZhaEmbeddings(
            vocab_size,
            hidden_size,
            hidden_dropout_prob,
            max_position_embeddings,
            type_vocab_size,
            use_relative_position
        )

        self.encoder = NeZhaEncoder(
            hidden_size,
            num_hidden_layers,
            num_attention_heads,
            intermediate_size,
            hidden_act,
            hidden_dropout_prob,
            attention_probs_dropout_prob,
            max_relative_position,
            layer_norm_eps
        )

        self.pooler = NeZhaPooler(hidden_size)
        self.apply(self.init_weights)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None):
        r'''
        The NeZhaModel forward method, overrides the `__call__()` special method.

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
                Defaults to ``None``, which means we don't add segment embeddings.
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
            Tuple: A tuple of shape (``sequence_output``, ``pooled_output``).

            With the fields:

            - sequence_output (`Tensor`):
                Sequence of hidden-states at the last layer of the model.
                It's data type should be float32 and has a shape of (`batch_size, seq_lens, hidden_size`].
                ``seq_lens`` corresponds to the length of input sequence.

            - pooled_output (`Tensor`):
                The output of first token (`[CLS]`) in sequence.
                We "pool" the model by simply taking the hidden state corresponding to the first token.
                Its data type should be float32 and
                has a shape of [batch_size, hidden_size].

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers import NeZhaModel, NeZhaTokenizer

                tokenizer = NeZhaTokenizer.from_pretrained('nezha-base-chinese')
                model = NeZhaModel.from_pretrained('nezha-base-chinese')

                inputs = tokenizer("这是一个测试样例")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                sequence_output, pooled_output = model(**inputs)

        '''

        if attention_mask is None:
            attention_mask = paddle.ones_like(input_ids)
        if token_type_ids is None:
            token_type_ids = paddle.zeros_like(input_ids)

        extended_attention_mask = attention_mask.unsqueeze(1).unsqueeze(2)
        extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0

        embedding_output = self.embeddings(input_ids, token_type_ids)

        encoder_outputs, _ = self.encoder(embedding_output, extended_attention_mask)

        sequence_output = encoder_outputs[-1]
        pooled_output = self.pooler(sequence_output)

        return sequence_output, pooled_output


class NeZhaLMPredictionHead(nn.Layer):
    def __init__(self,
                 hidden_size,
                 vocab_size,
                 hidden_act,
                 embedding_weights=None,
                 layer_norm_eps=1e-12):
        super(NeZhaLMPredictionHead, self).__init__()
        self.dense = nn.Linear(hidden_size, hidden_size)
        self.activation = ACT2FN[hidden_act]
        self.layer_norm = nn.LayerNorm(hidden_size, epsilon=layer_norm_eps)

        self.decoder_weight = embedding_weights
        self.decoder_bias = self.create_parameter(
            shape=[vocab_size], 
            dtype=self.decoder_weight.dtype, 
            is_bias=True
        )

    def forward(self, hidden_states):
        hidden_states = self.dense(hidden_states)
        hidden_states = self.activation(hidden_states)
        hidden_states = self.layer_norm(hidden_states)

        hidden_states = paddle.tensor.matmul(
            hidden_states, 
            self.decoder_weight,
            transpose_y=True
        ) + self.decoder_bias

        return hidden_states


class NeZhaPretrainingHeads(nn.Layer):
    """
    Perform language modeling task and next sentence classification task.

    Args:
        hidden_size (int):
            See :class:`NeZhaModel`.
        vocab_size (int):
            See :class:`NezhaModel`.
        hidden_act (str):
            Activation function used in hidden layers.
        embedding_weights (Tensor, optional):
            Embedding weights of the pretrained model. Defaults to `None`.

    """

    def __init__(self,
                 hidden_size,
                 vocab_size,
                 hidden_act,
                 embedding_weights=None):
        super(NeZhaPretrainingHeads, self).__init__()
        self.predictions = NeZhaLMPredictionHead(
            hidden_size, 
            vocab_size,
            hidden_act, 
            embedding_weights
        )
        self.seq_relationship = nn.Linear(hidden_size, 2)

    def forward(self, sequence_output, pooled_output):
        """
        Args:
            sequence_output(Tensor):
                Sequence of hidden-states at the last layer of the model.
                It's data type should be float32 and has a shape of (`batch_size, seq_lens, hidden_size`].
                ``seq_lens`` corresponds to the length of input sequence.
            pooled_output(Tensor):
                The output of first token (`[CLS]`) in sequence.
                We "pool" the model by simply taking the hidden state corresponding to the first token.
                Its data type should be float32 and
                has a shape of [batch_size, hidden_size].

        Returns:
            A tuple of shape (``prediction_scores``, ``seq_relationship_score``).

            With the fields:

            - `prediction_scores` (Tensor): The scores of prediction on masked token.
            - `seq_relationship_score` (Tensor): The scores of next sentence prediction.


        """
        prediction_scores = self.predictions(sequence_output)
        seq_relationship_score = self.seq_relationship(pooled_output)
        return prediction_scores, seq_relationship_score


class NeZhaForPretraining(NeZhaPretrainedModel):
    """
    NeZha Model for pretraining tasks on top.

    Args:
        nezha (:class:`NeZhaModel`):
            An instance of :class:`NeZhaModel`.

    """

    def __init__(self, nezha):
        super(NeZhaForPretraining, self).__init__()
        self.nezha = nezha
        self.cls = NeZhaPretrainingHeads(
            self.nezha.config["hidden_size"],
            self.nezha.config["vocab_size"],
            self.nezha.config["hidden_act"],
            self.nezha.embeddings.word_embeddings.weight
        )

        self.apply(self.init_weights)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None,
                masked_lm_labels=None, next_sentence_label=None):
        r"""

        Args:
            input_ids (Tensor):
                See :class:`NeZhaModel`.
            token_type_ids (Tensor, optional):
                See :class:`NeZhaModel`.
            attention_mask (Tensor, optional):
                See :class:`NeZhaModel`.
            masked_lm_labels(Tensor):
                The labels of the masked language modeling, the dimensionality of `masked_lm_labels`
                is equal to `prediction_scores`. Defaults to `None`.
            next_sentence_labels(Tensor):
                The labels of the next sentence prediction, the dimensionality of `next_sentence_labels`
                is equal to `seq_relation_lables`. Defaults to `None`.

        Returns:
            Tuple: A tuple of shape (``prediction_scores``, ``seq_relationship_score``).

            With the fields:

            - `prediction_scores` (Tensor): The scores of prediction on masked token.
            - `seq_relationship_score` (Tensor): The scores of next sentence prediction.

        """

        sequence_output, pooled_output = self.nezha(input_ids, token_type_ids, attention_mask)
        prediction_scores, seq_relationship_score = self.cls(sequence_output, pooled_output)

        if masked_lm_labels is not None and next_sentence_label is not None:
            loss_fct = nn.CrossEntropyLoss(ignore_index=-1)
            masked_lm_loss = loss_fct(prediction_scores.reshape(
                (-1, self.nezha.config["vocab_size"])), masked_lm_labels.reshape((-1,)))
            next_sentence_loss = loss_fct(seq_relationship_score.reshape(
                (-1, 2)), next_sentence_label.reshape((-1,)))
            total_loss = masked_lm_loss + next_sentence_loss
            return total_loss
        elif masked_lm_labels is not None:
            loss_fct = nn.CrossEntropyLoss(ignore_index=-1)
            masked_lm_loss = loss_fct(prediction_scores.reshape(
                (-1, self.nezha.config["vocab_size"])), masked_lm_labels.reshape((-1,)))
            total_loss = masked_lm_loss
            return total_loss
        else:
            return prediction_scores, seq_relationship_score


class NeZhaForQuestionAnswering(NeZhaPretrainedModel):
    r"""
    NeZha Model with a span classification head on top for extractive question-answering tasks like
    SQuAD (a linear layers on top of the hidden-states output to compute `span start logits` and
    `span end logits`).

    Args:
        nezha (:class:`NeZhaModel`):
            An instance of `paddlenlp.transformers.NeZhaModel`.
        dropout (float, optional):
            The dropout probability for output of NeZha.
            If None, use the same value as `hidden_dropout_prob`
            of `paddlenlp.transformers.NeZhaModel` instance. Defaults to `None`.
    """
    def __init__(self, nezha, dropout=None):
        super(NeZhaForQuestionAnswering, self).__init__()
        self.nezha = nezha
        self.classifier = nn.Linear(self.nezha.config["hidden_size"], 2)
        self.apply(self.init_weights)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None):
        r"""
        Args:
            input_ids (Tensor):
                See :class:`NeZhaModel`.
            token_type_ids (Tensor, optional):
                See :class:`NeZhaModel`.
            attention_mask (Tensor, optional):
                See :class:`NeZhaModel`.

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
                from paddlenlp.transformers import NeZhaForSequenceClassification, NeZhaTokenizer

                tokenizer = NeZhaTokenizer.from_pretrained('nezha-base-chinese')
                model = NeZhaForSequenceClassification.from_pretrained('nezha-base-chinese')

                inputs = tokenizer("这是一个测试样例")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)
                start_logits = outputs[0]
                end_logits = outputs[1]

        """

        sequence_output, _ = self.nezha(input_ids, token_type_ids, attention_mask)

        logits = self.classifier(sequence_output)
        logits = paddle.transpose(logits, perm=[2, 0, 1])

        start_logits, end_logits = paddle.unstack(x=logits, axis=0)

        return start_logits, end_logits


class NeZhaForSequenceClassification(NeZhaPretrainedModel):
    """
    Nezha Model with a sequence classification/regression head on top (a linear layer on top of the pooled output) e.g.
    for GLUE tasks.

    Args:
        nezha (:class:`NeZhaModel`):
            An instance of NeZhaModel.
        num_classes (int, optional):
            The number of classes. Default `2`.
        dropout (float, optional):
            The dropout probability for output of BERT.
            If None, use the same value as `hidden_dropout_prob` of `NeZhaModel`
            instance `nezha`. Default None.
    """
    def __init__(self, nezha, num_classes=2, dropout=None):
        super(NeZhaForSequenceClassification, self).__init__()
        self.num_classes = num_classes
        self.nezha = nezha
        self.dropout = nn.Dropout(dropout if dropout is not None else self.nezha.config["hidden_dropout_prob"])
        self.classifier = nn.Linear(self.nezha.config["hidden_size"], num_classes)
        self.apply(self.init_weights)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None):
        """
        The NeZhaForSequenceClassification forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`NeZhaModel`.
            token_type_ids (Tensor, optional):
                See :class:`NeZhaModel`.
            position_ids(Tensor, optional):
                See :class:`NeZhaModel`.
            attention_mask_list (list, optional):
                See :class:`NeZhaModel`.

        Returns:
            logits (Tensor):
                A Tensor of the input text classification logits.
                Shape as `(batch_size, num_classes)` and dtype as `float`.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers.nezha.modeling import NeZhaForSequenceClassification
                from paddlenlp.transformers.nezha.tokenizer import NeZhaTokenizer

                tokenizer = NeZhaTokenizer.from_pretrained('nezha-base-chinese')
                model = NeZhaForSequenceClassification.from_pretrained('nezha-base-chinese')

                inputs = tokenizer("这是一个测试样例")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)

                logits = outputs[0]
        """

        _, pooled_output = self.nezha(input_ids, token_type_ids, attention_mask)

        pooled_output = self.dropout(pooled_output)

        logits = self.classifier(pooled_output)

        return logits


class NeZhaForTokenClassification(NeZhaPretrainedModel):
    """
    NeZha Model with a token classification head on top (a linear layer on top of the hidden-states output) e.g.
    for Named-Entity-Recognition (NER) tasks.

    Args:
        nezha (:class:`NeZhaModel`):
            An instance of NeZhaModel.
        num_classes (int, optional):
            The number of classes. Default `2`.
        dropout (float, optional):
            The dropout probability for output of NeZha.
            If None, use the same value as `hidden_dropout_prob` of `NeZhaModel`
            instance `nezha`. Default None.
    """

    def __init__(self, nezha, num_classes=2, dropout=None):
        super(NeZhaForTokenClassification, self).__init__()
        self.num_classes = num_classes
        self.nezha = nezha 
        self.dropout = nn.Dropout(dropout if dropout is not None else self.nezha.config["hidden_dropout_prob"])
        self.classifier = nn.Linear(self.nezha.config["hidden_size"], num_classes)
        self.apply(self.init_weights)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None):
        r"""
        The NeZhaForSequenceClassification forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`NeZhaModel`.
            token_type_ids (Tensor, optional):
                See :class:`NeZhaModel`.
            position_ids(Tensor, optional):
                See :class:`NeZhaModel`.
            attention_mask_list (list, optional):
                See :class:`NeZhaModel`.

        Returns:
            logits (Tensor):
                A Tensor of the input text classification logits, shape as `(batch_size, seq_lens, num_classes)`.
                seq_lens mean the number of tokens of the input sequence.

        Example:
            .. code-block::

                import paddle
                from paddlenlp.transformers.nezha.modeling import NeZhaForTokenClassification
                from paddlenlp.transformers.nezha.tokenizer import NeZhaTokenizer

                tokenizer = NeZhaTokenizer.from_pretrained('nezha-base-chinese')
                model = NeZhaForTokenClassification.from_pretrained('nezha-base-chinese')

                inputs = tokenizer("这是一个测试样例!")
                inputs = {k:paddle.to_tensor([v]) for (k, v) in inputs.items()}
                outputs = model(**inputs)

                logits = outputs[0]
        """

        sequence_output, _ = self.nezha(input_ids, token_type_ids, attention_mask)
        sequence_output = self.dropout(sequence_output)

        logits = self.classifier(sequence_output)

        return logits


class NeZhaForMultipleChoice(NeZhaPretrainedModel):
    """
    NeZha Model with a multiple choice classification head on top (a linear layer on top of the pooled output and a
    softmax) e.g. for RocStories/SWAG tasks..

    Args:
        nezha (:class:`NeZhaModel`):
            An instance of NeZhaModel.
        num_classes (int, optional):
            The number of classes. Default `2`.
        dropout (float, optional):
            The dropout probability for output of NeZha.
            If None, use the same value as `hidden_dropout_prob` of `NeZhaModel`
            instance `nezha`. Default None.
    """
    def __init__(self, nezha, num_choices=2, dropout=None):
        super(NeZhaForMultipleChoice, self).__init__()
        self.num_choices = num_choices
        self.nezha = nezha
        self.dropout = nn.Dropout(dropout if dropout is not None else self.nezha.config["hidden_dropout_prob"])
        self.classifier = nn.Linear(self.nezha.config["hidden_size"], 1)
        self.apply(self.init_weights)

    def forward(self, input_ids, token_type_ids=None, attention_mask=None):
        r"""
        The NeZhaForSequenceClassification forward method, overrides the __call__() special method.

        Args:
            input_ids (Tensor):
                See :class:`NeZhaModel`.
            token_type_ids (Tensor, optional):
                See :class:`NeZhaModel`.
            attention_mask(Tensor, optional):
                See :class:`NeZhaModel`.

        Returns:
            reshaped_logits (Tensor): The reshaped logits of the claissifer.

        """

        # input_ids: [bs, num_choice, seq_l]
        input_ids = input_ids.reshape((-1, input_ids.shape[-1]))  # flat_input_ids: [bs*num_choice,seq_l]
        
        if token_type_ids:
            token_type_ids = token_type_ids.reshape((-1, token_type_ids.shape[-1]))
        if attention_mask:
            attention_mask = attention_mask.reshape((-1, attention_mask.shape[-1]))

        _, pooled_output = self.nezha(input_ids, token_type_ids, attention_mask)
        pooled_output = self.dropout(pooled_output)
    
        logits = self.classifier(pooled_output)  # logits: (bs*num_choice,1)
        reshaped_logits = logits.reshape((-1, self.num_choices))  # logits: (bs, num_choice)

        return reshaped_logits
