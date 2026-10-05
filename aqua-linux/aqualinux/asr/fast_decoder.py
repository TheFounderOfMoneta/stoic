"""Greedy RNN-T: cached label-prefix states and reusable CUDA graphs.

The checkpoint, tokenizer and max-symbols policy remain unchanged. Validate
against the official decoder when upgrading GigaAM or PyTorch.
"""
import torch


class GraphRNNT:
    @torch.inference_mode()
    def __init__(self, head, decoding, batch_size, steps=4, max_frames=640):
        self.head, self.decoding = head, decoding
        self.batch_size, self.steps, self.max_frames = batch_size, steps, max_frames
        self.device = next(head.parameters()).device
        self.blank = decoding.blank_id
        self.max_symbols = decoding.max_symbols
        b, d = batch_size, head.joint.joint_hidden if hasattr(head.joint, 'joint_hidden') else head.joint.enc.out_features
        self.frames = torch.zeros((b, max_frames, d), device=self.device)
        self.lengths = torch.zeros(b, dtype=torch.long, device=self.device)
        self.rows = torch.arange(b, device=self.device)
        self.times = torch.zeros(b, dtype=torch.long, device=self.device)
        self.symbols = torch.zeros_like(self.times)
        self.counts = torch.zeros_like(self.times)
        self.tokens = torch.full((b, max_frames * self.max_symbols + 1), self.blank, dtype=torch.long, device=self.device)
        self.token_frames = torch.zeros_like(self.tokens)
        g, (h, c) = head.decoder.predict(None, None, batch_size=b)
        self.h, self.c = h.clone(), c.clone()
        self.pred = head.joint.pred(g[:, 0]).clone()
        self.done = torch.zeros((), dtype=torch.bool, device=self.device)
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream):
            for _ in range(3):
                self.step()
        torch.cuda.current_stream().wait_stream(stream)
        self.graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(self.graph, stream=stream):
            for _ in range(steps):
                self.step()

    def step(self):
        active = self.times < self.lengths
        f = self.frames[self.rows, self.times.clamp(max=self.max_frames - 1)]
        # argmax(log_softmax(logits)) == argmax(logits).
        k = self.head.joint.joint_net(f + self.pred).argmax(-1)
        emit = active & (k != self.blank)
        self.tokens[self.rows, self.counts] = k
        self.token_frames[self.rows, self.counts] = self.times
        self.counts.add_(emit.long())
        # The predictor depends on the label prefix, not on acoustic time.
        g, (h, c) = self.head.decoder.predict(k[:, None], (self.h, self.c), self.batch_size)
        self.h.copy_(torch.where(emit[None, :, None], h, self.h))
        self.c.copy_(torch.where(emit[None, :, None], c, self.c))
        pred = self.head.joint.pred(g[:, 0])
        self.pred.copy_(torch.where(emit[:, None], pred, self.pred))
        symbols = self.symbols + emit.long()
        advance = active & ((~emit) | (symbols >= self.max_symbols))
        self.times.add_(advance.long())
        self.symbols.copy_(torch.where(advance, 0, symbols))
        self.done.copy_((self.times >= self.lengths).all())

    @torch.inference_mode()
    def decode(self, encoded, lengths):
        b, _, t = encoded.shape
        if b != self.batch_size or t > self.max_frames:
            raise ValueError('RNN-T input exceeds the captured graph dimensions')
        projected = self.head.joint.enc(encoded.transpose(1, 2))
        self.frames[:, :t].copy_(projected)
        self.lengths.copy_(lengths)
        self.times.zero_(); self.symbols.zero_(); self.counts.zero_()
        g, (h, c) = self.head.decoder.predict(None, None, batch_size=b)
        self.h.copy_(h); self.c.copy_(c)
        self.pred.copy_(self.head.joint.pred(g[:, 0]))
        self.done.zero_()
        for _ in range((t * self.max_symbols + self.steps - 1) // self.steps + 1):
            self.graph.replay()
            if self.done.item():
                break
        else:
            raise RuntimeError('RNN-T exceeded the greedy decoding bound')
        counts = self.counts.tolist()
        tokens = self.tokens.cpu()
        frames = self.token_frames.cpu()
        result = []
        for i, n in enumerate(counts):
            ids = tokens[i, :n].tolist()
            result.append((self.decoding.tokenizer.decode(ids), ids, frames[i, :n].tolist()))
        return result
