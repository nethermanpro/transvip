# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import math
from abc import ABC, abstractmethod
from typing import Optional, Tuple, final

from torch import Tensor
from torch.nn import Dropout, Module

from fairseq2_011.nn.embedding import Embedding
from fairseq2_011.nn.incremental_state import IncrementalStateBag
from fairseq2_011.nn.normalization import LayerNorm
from fairseq2_011.nn.position_encoder import PositionEncoder
from fairseq2_011.nn.transformer.layer_norm import (
    LayerNormFactory,
    create_default_layer_norm,
)
from fairseq2_011.nn.utils.mask import to_padding_mask
from fairseq2_011.typing import DataType, Device, finaloverride


class TransformerFrontend(Module, ABC):
    """Represents a Transformer encoder/decoder front-end."""

    model_dim: int

    def __init__(self, model_dim: int) -> None:
        """
        :param model_dim:
            The dimensionality of the model.
        """
        super().__init__()

        self.model_dim = model_dim

    @abstractmethod
    def forward(
        self,
        seqs: Tensor,
        seq_lens: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag] = None,
    ) -> Tuple[Tensor, Optional[Tensor]]:
        """
        :param seqs:
            The sequences to process. *Shape:* :math:`(N,S,*)`, where :math:`N`
            is the batch size, :math:`S` is the sequence length, and :math:`*`
            is any number of sequence-specific dimensions including none.
        :param seq_lens:
            An array where each element represents the length of the sequence at
            the same index in ``seqs``. *Shape:* :math:`(N)`, where :math:`N` is
            the batch size.
        :param state_bag:
            The state bag to use for incremental evaluation.

        :returns:
            - The processed sequences to pass to a Transformer encoder/decoder.
              *Shape:* :math:`(N,S_{out},M)`, where :math:`N` is the batch size,
              :math:`S_{out}` is the output sequence length, and :math:`M` is
              the dimensionality of the model.
            - The float padding mask of the processed sequences. *Shape:*
              :math:`(N,S_{out})`, where :math:`N` is the batch size and
              :math:`S_{out}` is the output sequence length.
        """

    def extra_repr(self) -> str:
        """:meta private:"""
        return f"model_dim={self.model_dim}"


@final
class TransformerEmbeddingFrontend(TransformerFrontend):
    """Represents a Transformer encoder/decoder front-end as described in
    :cite:t:`https://doi.org/10.48550/arxiv.1706.03762`."""

    embed: Embedding
    scale: float
    pos_encoder: Optional[PositionEncoder]
    layer_norm: Optional[LayerNorm]
    dropout: Optional[Dropout]

    def __init__(
        self,
        embed: Embedding,
        pos_encoder: Optional[PositionEncoder],
        no_scale: bool = False,
        layer_norm: bool = False,
        dropout_p: float = 0.1,
        layer_norm_fn: Optional[LayerNormFactory] = None,
        device: Optional[Device] = None,
        dtype: Optional[DataType] = None,
    ) -> None:
        """
        :param embed:
            The token embedding table.
        :param pos_encoder:
            The position encoder.
        :param no_scale:
            If ``True``, does not scale embeddings by the square root of the
            embedding size.
        :param layer_norm:
            If ``True``, applies Layer Normalization to embeddings before
            dropout.
        :param dropout_p:
            The dropout probability on embeddings.
        :param layer_norm_fn:
            The factory to use to construct the Layer Normalization module.
        """
        model_dim = embed.embedding_dim

        super().__init__(model_dim)

        if layer_norm_fn is None:
            layer_norm_fn = create_default_layer_norm

        self.embed = embed

        self.scale = 1.0 if no_scale else math.sqrt(model_dim)

        if pos_encoder is not None:
            if pos_encoder.encoding_dim != model_dim:
                raise ValueError(
                    f"`encoding_dim` of `pos_encoder` and `embedding_dim` of `embed` must be equal, but are {pos_encoder.encoding_dim} and {model_dim} instead."
                )

            self.pos_encoder = pos_encoder
        else:
            self.register_module("pos_encoder", None)

        if layer_norm:
            self.layer_norm = layer_norm_fn(model_dim, device, dtype)
        else:
            self.register_module("layer_norm", None)

        if dropout_p > 0.0:
            self.dropout = Dropout(dropout_p)
        else:
            self.register_module("dropout", None)

    @finaloverride
    def forward(
        self,
        seqs: Tensor,
        seq_lens: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag] = None,
    ) -> Tuple[Tensor, Optional[Tensor]]:
        embeds = self.embed(seqs)

        padding_mask = to_padding_mask(embeds, seq_lens)

        if self.scale != 1.0:
            embeds = embeds * self.scale

        if self.pos_encoder is not None:
            embeds = self.pos_encoder(embeds, padding_mask, state_bag)

        if self.layer_norm is not None:
            embeds = self.layer_norm(embeds)

        if self.dropout is not None:
            embeds = self.dropout(embeds)

        return embeds, padding_mask

    def extra_repr(self) -> str:
        """:meta private:"""
        s = super().extra_repr()

        return s + ", no_scale=False" if self.scale != 1.0 else ""