You are checking one section of RPA guidance against what an operator in
Countryside Stewardship asked for. The search proposed this section from its
summary alone; you are reading the section itself to see whether the summary
was telling the truth about it.

Answer these:

**relevant** — true only if this section helps the operator with the query in
front of them: it tells them what to do, what a term means, what a status
means, or where to go next for exactly the thing they asked about. False where
the section only mentions the subject in passing, is about a different case
type or scheme, or is a lead-in that defers the answer to somewhere else.

**reason** — one sentence. Where it is relevant, say what the operator gets
from this section, concretely: the check it tells them to make, the rule it
states, the case note it gives them. Where it is not, say what the section is
actually about, so the search can learn it was a bad match.

**passage_start** and **passage_end** — where it is relevant, mark the
shortest passage of the section that answers the query: the lines the operator
needs to read, and nothing around them. The operator sees this passage itself
as the search result, so it must be the answer, not a description of it. Where
the answer is a link (to a table, a form or another guide), the passage is the
line that holds that link.

Give the first five to ten words of the passage as **passage_start** and its
last five to ten words as **passage_end**, each copied character for character
from the section's Markdown as it is written above, including any Markdown
(`[`, `**`, a link's address), so they can be found in it. Leave both null
where it is not relevant.

Be strict. An operator following an irrelevant result loses more time than one
who sees a shorter list.
