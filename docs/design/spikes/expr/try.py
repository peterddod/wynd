from pathlib import Path
from lark import Lark
from lark.exceptions import UnexpectedInput
g = Path(__file__).with_name("grammar.lark").read_text()
p = Lark(g, parser="lalr", propagate_positions=True, maybe_placeholders=True)
cases = [
 'steps.validate.outputs.valid',
 'steps.validate.outputs.fixable and steps.fix.runs < 3',
 'if steps.classify.outputs.kind == "invoice" then env.INVOICE_DIR\n elif steps.classify.outputs.kind == "receipt" then env.RECEIPT_DIR\n else env.MISC_DIR',
 'default(steps.extract.outputs.label, "unlabelled")',
 'edges["validate.done"][0].taken',
 'edges["validate.done"].retry.taken',
 'not a in b',
 'a not in b',
 '-1 - -2 * 3 % 4',
 'if a then if b then 1 else 2 elif c then 3 else 4',
 '[1, 2, 3,]',
 '{a: 1, "b-c": [true, null]}',
 'now()',
 'x.items[0].sku',
 "'single' + \"double\"",
 'steps.x.outputs.if_',
 '(if a then 1 else 2) + 1',
 'len(steps.x.outputs.items) > 0 or false',
]
for c in cases:
    t = p.parse(c)
    print(repr(c)[:60].ljust(62), t.data if hasattr(t,"data") else t)
bad = ['a < b < c', 'if a then b', '1 +', 'steps.x.in', 'a ==', 'unlabelled here', 'x + if a then 1 else 2', 'f(a)(b)']
for c in bad:
    try:
        t = p.parse(c); print("PARSED(!)", repr(c), t)
    except UnexpectedInput as e:
        print("ERR", repr(c).ljust(28), type(e).__name__, e.line, e.column, sorted(getattr(e, 'expected', None) or getattr(e,'allowed', None) or [])[:8])
