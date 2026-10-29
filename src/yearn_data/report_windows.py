"""Bounded provider-aware report reads. Only complete results escape a window."""
import time

import requests


LIMITS = ('block range too', 'block range limit', 'too many results',
          'query returned more', 'response size exceeded', 'log response size')


def read_windows(fetch, lo, hi, source):
    # The caller's chunk remains an upper bound. Sparse Envio reads can grow
    # much larger than direct RPC scans; a slow successful query shrinks next.
    ceiling = 1_000_000 if source=='envio' else 50_000
    width = min(ceiling, 250_000 if source=='envio' else 10_000)
    floor = min(width, hi-lo+1)
    def read(first,last,timeout_splits=0):
        try:
            return fetch(first,last)
        except Exception as exc:
            message=str(exc).lower()
            cause=exc
            timeout=False
            while cause is not None:
                timeout |= isinstance(cause,requests.Timeout)
                cause=cause.__cause__
            limited=any(term in message for term in LIMITS)
            timeout |= 'query timeout' in message or 'request timed out' in message or 'envio http 504' in message
            if first==last or not (limited or (timeout and timeout_splits<2)):
                raise
            middle=(first+last)//2
            splits=timeout_splits+int(timeout)
            return read(first,middle,splits)+read(middle+1,last,splits)
    rows=[]
    while lo<=hi:
        end=min(hi,lo+width-1)
        started=time.monotonic()
        result=read(lo,end)
        elapsed=time.monotonic()-started
        rows.extend(result)
        if elapsed<1 and len(result)<1000:width=min(ceiling,width*2)
        elif elapsed>3:width=max(floor,width//2)
        lo=end+1
    return rows
