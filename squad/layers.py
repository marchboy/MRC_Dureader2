import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from .util import masked_softmax


class Embedding(nn.Module):
    """Embedding layer used by BiDAF, without the character-level component.

    Word-level embeddings are further refined using a 2-layer Highway Encoder
    (see `HighwayEncoder` class for details).

    Args:
        word_vectors (torch.Tensor): Pre-trained word vectors.
        hidden_size (int): Size of hidden activations.
        drop_prob (float): Probability of zero-ing out activations
    """
    def __init__(self, word_vectors, char_vectors, num_filters, kernel_size,
                 hidden_size, drop_prob):
        super(Embedding, self).__init__()
        self.drop_prob = drop_prob
        # ----------------------------------------------------------------------------------------
        # TODO
        # 补全代码 1
        # 使用 GloVe/Word2vec embedding 和 CNN char embedding 
        # 来初始化 embedding 层
        # ----------------------------------------------------------------------------------------
        
        self.embed = nn.Embedding.from_pretrained(word_vectors)
        self.conv1 = nn.Conv1d(hidden_size, char_vectors.size(1), num_filters, kernel_size)
    
        self.proj = nn.Linear(word_vectors.size(1)+448, hidden_size, bias=False) 
        self.hwy = HighwayEncoder(2, hidden_size)

    def forward(self, x, char_x):
        # ----------------------------------------------------------------------------------------
        # TODO
        # 补全代码 1
        # 使用 GloVe/Word2vec embedding 和 CNN char embedding 
        # 来初始化 embedding 层
        # ----------------------------------------------------------------------------------------
        
        emb = self.embed(x)   # (batch_size, seq_len, embed_size)
        
        emb = self.proj(emb)  # (batch_size, seq_len, hidden_size)
        emb = F.dropout(emb, self.drop_prob, self.training) # 对 word embedding dropout 就和普通的 dropout 意义一样吗
        
        batch_size, sentence_length, max_word_length = char_x.size()
        c = char_x.contiguous().view(-1, max_word_length)     
        c = self.char_embed(c)
        c = F.dropout(c, self.drop_prob, self.training)
        c_emb = self.cnn(c.permute(0, 2, 1), sentence_length, batch_size) 
        c_emb_avg = self.avgatt(c_emb) # weighted average char embedding
        c_emb = torch.cat((c_emb, c_emb_avg), dim=2)

        emb = torch.cat((c_emb, emb), 2)

        emb = self.hwy(emb)   # (batch_size, seq_len, hidden_size)
        return emb


class CharEmbedding(nn.Module):
    """character-level embedding
    using CNN and max-pooling to obtain a fixed size vector
    """

    def __init__(self, char_vectors, hidden_size, kernel_size):
        super(CharEmbedding, self).__init__()
        self.embed = nn.Embedding.from_pretrained(char_vectors, freeze=False)
        self.char_embed_size = self.embed.weight.size(1)
        self.hiddens = [64, 128, 256]
        self.kernels = [3, 5, 7]
        self.convs = nn.ModuleList(
            nn.Conv2d(self.char_embed_size, hidden, (1, kernel))
            for hidden, kernel in zip(self.hiddens, self.kernels)
        )
        # self.cnn = nn.Conv2d(self.char_embed_size, hidden_size, (1, kernel_size))
        self.cnn_kernel_size = kernel_size

    def forward(self, x):
        """
        Args:
            x: shape: (bs, seq_len, word_len)
        Returns: character-level embedding, shape: (bs, seq_len, hidden_size)
        """
        emb = self.embed(x) # (bs, seq_len, word_len, char_embed)
        bs, seq_len, word_len, _ = emb.size()
        emb = emb.permute(0, 3, 1, 2).contiguous()
        embs = []
        for conv, kernel_size in zip(self.convs, self.kernels):
            emb_conv = F.relu(conv(emb))
            emb_pool = F.max_pool2d(
                emb_conv, kernel_size=(1,word_len-kernel_size+1)
            ).squeeze(-1)
            emb_pool = emb_pool.permute(0, 2, 1)
            embs.append(emb_pool)
        emb = torch.cat(embs, dim=-1).contiguous()
        return emb

class HighwayEncoder(nn.Module):
    """Encode an input sequence using a highway network.

    Based on the paper:
    "Highway Networks"
    by Rupesh Kumar Srivastava, Klaus Greff, Jürgen Schmidhuber
    (https://arxiv.org/abs/1505.00387).

    Args:
        num_layers (int): Number of layers in the highway encoder.
        hidden_size (int): Size of hidden activations.
    """
    def __init__(self, num_layers, hidden_size):
        super(HighwayEncoder, self).__init__()
        self.transforms = nn.ModuleList([nn.Linear(hidden_size, hidden_size)
                                         for _ in range(num_layers)])
        self.gates = nn.ModuleList([nn.Linear(hidden_size, hidden_size)
                                    for _ in range(num_layers)])

    def forward(self, x):
        # ----------------------------------------------------------------------------------------
        # TODO
        # 补全代码 2
        # 使用 self.transforms 和 self.gates 来实现 highway 的结构
        # ----------------------------------------------------------------------------------------

        # ===================以下为补全的代码======================================================
        for transform, gate in zip(self.transforms, self.gates):
            # Shapes of g, t, and x are all (batch_size, seq_len, hidden_size)
            h = torch.sigmoid(transform(x))
            z = torch.sigmoid(gate(x))
            x = z * h + (1 - z) * x


        return x


class RNNEncoder(nn.Module):
    """General-purpose layer for encoding a sequence using a bidirectional RNN.

    Encoded output is the RNN's hidden state at each position, which
    has shape `(batch_size, seq_len, hidden_size * 2)`.

    Args:
        input_size (int): Size of a single timestep in the input.
        hidden_size (int): Size of the RNN hidden state.
        num_layers (int): Number of layers of RNN cells to use.
        drop_prob (float): Probability of zero-ing out activations.
    """
    def __init__(self,
                 input_size,
                 hidden_size,
                 num_layers,
                 drop_prob=0.):
        super(RNNEncoder, self).__init__()
        self.drop_prob = drop_prob
        self.rnn = nn.LSTM(input_size, hidden_size, num_layers,
                           batch_first=True,
                           bidirectional=True,
                           dropout=drop_prob if num_layers > 1 else 0.)

    def forward(self, x, lengths):
        # Save original padded length for use by pad_packed_sequence
        orig_len = x.size(1)

        # Sort by length and pack sequence for RNN
        # print(lengths.device)
        lengths, sort_idx = lengths.sort(0, descending=True)
        x = x[sort_idx]     # (batch_size, seq_len, input_size)
        x = pack_padded_sequence(x, lengths, batch_first=True)

        # Apply RNN
        self.rnn.flatten_parameters()
        x, _ = self.rnn(x)  # (batch_size, seq_len, 2 * hidden_size)

        # Unpack and reverse sort
        x, _ = pad_packed_sequence(x, batch_first=True, total_length=orig_len)
        _, unsort_idx = sort_idx.sort(0) # 还原顺序
        x = x[unsort_idx]   # (batch_size, seq_len, 2 * hidden_size)

        # Apply dropout (RNN applies dropout after all but the last layer)
        x = F.dropout(x, self.drop_prob, self.training)

        return x


class BiDAFAttention(nn.Module):
    """Bidirectional attention originally used by BiDAF.

    Bidirectional attention computes attention in two directions:
    The context attends to the query and the query attends to the context.
    The output of this layer is the concatenation of [context, c2q_attention,
    context * c2q_attention, context * q2c_attention]. This concatenation allows
    the attention vector at each timestep, along with the embeddings from
    previous layers, to flow through the attention layer to the modeling layer.
    The output has shape (batch_size, context_len, 8 * hidden_size).

    Args:
        hidden_size (int): Size of hidden activations.
        drop_prob (float): Probability of zero-ing out activations.
    """
    def __init__(self, hidden_size, drop_prob=0.1):
        super(BiDAFAttention, self).__init__()
        self.drop_prob = drop_prob
        self.c_weight = nn.Parameter(torch.zeros(hidden_size, 1), requires_grad=True)
        self.q_weight = nn.Parameter(torch.zeros(hidden_size, 1), requires_grad=True)
        self.cq_weight = nn.Parameter(torch.zeros(1, 1, hidden_size), requires_grad=True)
        for weight in (self.c_weight, self.q_weight, self.cq_weight):
            nn.init.xavier_uniform_(weight)
        self.bias = nn.Parameter(torch.zeros(1), requires_grad=True)

    def forward(self, c, q, c_mask, q_mask):
        batch_size, c_len, _ = c.size()
        q_len = q.size(1)
        s = self.get_similarity_matrix(c, q)        # (batch_size, c_len, q_len)
        c_mask = c_mask.view(batch_size, c_len, 1)  # (batch_size, c_len, 1)
        q_mask = q_mask.view(batch_size, 1, q_len)  # (batch_size, 1, q_len)
        s1 = masked_softmax(s, q_mask, dim=2)       # (batch_size, c_len, q_len)
        s2 = masked_softmax(s, c_mask, dim=1)       # (batch_size, c_len, q_len)

        # (bs, c_len, q_len) x (bs, q_len, hid_size) => (bs, c_len, hid_size)
        a = torch.bmm(s1, q)
        # (bs, c_len, c_len) x (bs, c_len, hid_size) => (bs, c_len, hid_size)
        b = torch.bmm(torch.bmm(s1, s2.transpose(1, 2)), c)

        x = torch.cat([c, a, c * a, c * b], dim=2)  # (bs, c_len, 4 * hid_size)

        return x

    def get_similarity_matrix(self, c, q):
        """Get the "similarity matrix" between context and query (using the
        terminology of the BiDAF paper).

        A naive implementation as described in BiDAF would concatenate the
        three vectors then project the result with a single weight matrix. This
        method is a more memory-efficient implementation of the same operation.

        See Also:
            Equation 1 in https://arxiv.org/abs/1611.01603
        """
        c_len, q_len = c.size(1), q.size(1)
        c = F.dropout(c, self.drop_prob, self.training)  # (bs, c_len, hid_size)
        q = F.dropout(q, self.drop_prob, self.training)  # (bs, q_len, hid_size)

        # Shapes: (batch_size, c_len, q_len) , (bs, c_len, 1)
        s0 = torch.matmul(c, self.c_weight).expand([-1, -1, q_len]) # (bs, c_len, q_len) 按列 broadcast, 复制了 q_len 列
        # (bs, q_len, 1)
        s1 = torch.matmul(q, self.q_weight).transpose(1, 2)\
                                           .expand([-1, c_len, -1])
        # c * self.cq_weight shape (bs, c_len, hidden_size)
        s2 = torch.matmul(c * self.cq_weight, q.transpose(1, 2))
        s = s0 + s1 + s2 + self.bias

        return s


class BiDAFOutput(nn.Module):
    """Output layer used by BiDAF for question answering.

    Computes a linear transformation of the attention and modeling
    outputs, then takes the softmax of the result to get the start pointer.
    A bidirectional LSTM is then applied the modeling output to produce `mod_2`.
    A second linear+softmax of the attention output and `mod_2` is used
    to get the end pointer.

    Args:
        hidden_size (int): Hidden size used in the BiDAF model.
        drop_prob (float): Probability of zero-ing out activations.
    """
    def __init__(self, hidden_size, drop_prob):
        super(BiDAFOutput, self).__init__()
        self.att_linear_1 = nn.Linear(8 * hidden_size, 1)
        self.mod_linear_1 = nn.Linear(2 * hidden_size, 1)

        self.rnn = RNNEncoder(input_size=2 * hidden_size,
                              hidden_size=hidden_size,
                              num_layers=1,
                              drop_prob=drop_prob)

        self.att_linear_2 = nn.Linear(8 * hidden_size, 1)
        self.mod_linear_2 = nn.Linear(2 * hidden_size, 1)

    def forward(self, att, mod, mask):
        # Shapes: (batch_size, seq_len, 1)
        logits_1 = self.att_linear_1(att) + self.mod_linear_1(mod)
        mod_2 = self.rnn(mod, mask.sum(-1).cpu())
        logits_2 = self.att_linear_2(att) + self.mod_linear_2(mod_2)

        # Shapes: (batch_size, seq_len)
        log_p1 = masked_softmax(logits_1.squeeze(), mask, log_softmax=True)
        log_p2 = masked_softmax(logits_2.squeeze(), mask, log_softmax=True)

        return log_p1, log_p2
