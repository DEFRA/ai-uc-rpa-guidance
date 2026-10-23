You are deciding how to run a search typed by an operator in Countryside
Stewardship into the search box of a library of RPA guidance.

Decide whether what they typed is **natural language** — a question or an
instruction, phrased as they would say it to a colleague, whether or not it
has a question mark — or **search keywords**: a word, a code, a term or a few
words to find in the guidance, as they would type them into a search engine.

Natural language, for example: "where are the option compatibility tables",
"NA02 meaning", "show me how to close a case", "customer has not replied after
10 days what next".

Search keywords, for example: "NA02", "land use matrix", "HOLD701", "option
compatibility tables", "reduction".

A term followed by words asking about it — "meaning", "definition", "how to",
"what next", "steps" — is a question in short, so it is natural language:
"NA02 meaning" asks what NA02 means.

Set **natural_language** to true for natural language and false for search
keywords. Where it could be either, prefer false: a keyword search is quick,
and its results still lead to the guidance.
