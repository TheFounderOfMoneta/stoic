"""Dynamic per-token/per-output-channel INT8 encoder linear layers."""
import torch
from torch import nn
import torch.nn.functional as F


class Int8Linear(nn.Module):
    def __init__(self, linear):
        super().__init__()
        w=linear.weight.detach().float()
        scale=w.abs().amax(dim=1).clamp(min=1e-8)/127
        q=torch.round(w/scale[:,None]).clamp(-127,127).to(torch.int8)
        self.register_buffer('weight',q.t())
        self.register_buffer('scale',scale)
        self.register_buffer('bias',linear.bias.detach().float().clone() if linear.bias is not None else None)
        self.in_features,self.out_features=linear.in_features,linear.out_features

    def forward(self,x):
        shape=x.shape[:-1]
        a=x.reshape(-1,x.shape[-1]).float()
        scale=a.abs().amax(dim=1,keepdim=True).clamp(min=1e-8)/127
        aq=torch.round(a/scale).clamp(-127,127).to(torch.int8)
        pad=(-aq.shape[0])%8
        if pad: aq=F.pad(aq,(0,0,0,pad))
        y=torch._int_mm(aq,self.weight)[:a.shape[0]].float()
        y=y*(scale*self.scale[None])
        if self.bias is not None: y=y+self.bias
        return y.reshape(*shape,self.out_features)


def quantize_encoder(module, feedforward_only=False):
    for name,child in list(module.named_children()):
        if isinstance(child,nn.Linear) and child.in_features%16==0 and child.out_features%8==0:
            if not feedforward_only or name in ('linear1','linear2'):
                setattr(module,name,Int8Linear(child))
        else:
            quantize_encoder(child,feedforward_only)
