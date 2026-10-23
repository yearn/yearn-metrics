# yvUSD effective product fees

The combined yvUSD entry retains its unlocked and visible locked roots, member
addresses, and summed external capital. Its performance and annual management
rates now include downstream fees, using the existing effective stack rate for
each member, weighted by that member's external capital. Gross member capital is
not summed as the weight because it repeats the locked shares held in yvUSD.

Unknown rates on funded members or unknown capital make the corresponding
effective rate unavailable. A known-zero-weight member does not make another
member's known rate unavailable. Performance and management rates are combined
independently. Individual nodes retain their own rates; the combined effective
rate must not be presented as the selected vault layer's own charge. The yBOLD
own-rate policy and historical paid-fee accounting are unchanged. When no unlocked
yvUSD entry exists (including when its gross TVL is missing), visible lockers
keep their verified own rates despite incomplete allocation history. Unknown
external capital alone does not remove an otherwise present yvUSD entry: its
product remains grouped with unavailable rates and adjusted TVL, while component
roots retain their own fee configuration and availability.

Analytics methodology `powerglove-analytics-18` invalidates publications prepared
under the old own-fee override. Reprepare analytics against the selected TVL and
financial publications before serving the new result. This does not require a
TVL/history rebuild or financial fee recollection.

This change updates analytics entries only. Deploy it with the existing canonical
ownership accounting intact and prepare a new analytics publication before
selecting it. No TVL modules, gross capital, historical paid fees, or product
membership are changed. The fee regressions were also run against a copy of the
current deployed ownership-accounting runtime.
