You are indexing one RPA guidance document section by section, so that a
caseworker searching the corpus is taken to the section that answers them
rather than to the document that contains it.

The whole document is given below as Markdown, converted from Word, together
with the list of sections the parse found. Work from that list: return one
entry for every section on it, using its number exactly as given, and no
entries for anything else.

For each section return:

**summary** — what that section covers and when a reader turns to it, in one
or two sentences. Shorter than a summary of the whole document: a reader is
scanning a list of these to choose one.

**keywords** — the terms someone would search by to reach this section.

**acronyms** — every acronym this section uses, with what it stands for where
this document says so. Where the document uses one but never expands it
anywhere, give the acronym with a null expansion rather than leaving it out or
guessing: a reader searching for it still has to be brought here. Include an
initialism the section's own subject would be called by — "Revenue Option
Claim Rule" is ROCR — as well as the ones written in the text.

Rules:

- Write plain English prose for the summary. It is read by a person choosing
  where to go, so no note form, no heading, no repeating the section's title
  back — it is already shown beside it.
- Put the searchable terms into the prose as well as into the keywords: the
  scheme, option codes, case types, systems, forms and acronyms the section
  names. Someone searching for "SSSI" or "HOLD806" should land here because
  the sentence says so, not only because a keyword list does.
- Give an acronym as the section writes it, and add its expansion where the
  document defines one, so that either spelling finds it. Every acronym you
  list under `acronyms` belongs in the prose or the keywords too — the
  keywords are what a keyword search reads.
- Describe only what that section contains. A section that is a short lead-in
  to its sub-sections should say so — that is a useful thing for a searcher
  to know, and inventing content for it is not.
- A section with a heading and no body of its own still gets an entry: say
  what it groups.
