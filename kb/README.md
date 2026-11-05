# Knowledge base corpus

`corpus.jsonl` holds extracts of Simple English Wikipedia articles on three topics: space, geography
and Nobel-winning science. They come from the 2023-11-01 snapshot published at
[wikimedia/wikipedia](https://huggingface.co/datasets/wikimedia/wikipedia), and each line records the
article's title and URL.

The text is licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Long
articles are truncated. See `build_corpus.py` to rebuild the file. Facts reflect the snapshot's date
and may be out of date.
