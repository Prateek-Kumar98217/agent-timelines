"""Build kb/corpus.jsonl from a Simple English Wikipedia snapshot. Only needed to refresh the file.

Uses a published snapshot instead of live requests, as Wikimedia's robot policy prefers. Download
`20231101.simple/train-00000-of-00001.parquet` from huggingface.co/datasets/wikimedia/wikipedia:

    uv run --with pyarrow python kb/build_corpus.py path/to/snapshot.parquet
"""

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

MAX_CHARS = 15_000
MIN_CHARS = 300

SPACE = """Sun|Mercury (planet)|Venus|Earth|Mars|Jupiter|Saturn|Uranus|Neptune|Pluto|Ceres (dwarf planet)
Eris (dwarf planet)|Makemake|Haumea|Moon|Io (moon)|Europa (moon)|Ganymede (moon)|Callisto (moon)
Titan (moon)|Enceladus|Triton (moon)|Phobos (moon)|Deimos (moon)|Solar System|Asteroid belt
Kuiper belt|Oort cloud|Halley's Comet|Comet|Asteroid|Meteorite|Milky Way|Andromeda Galaxy|Galaxy
Black hole|Neutron star|Supernova|White dwarf|Red giant|Star|Light-year|Big Bang|Exoplanet
Apollo program|Apollo 8|Apollo 11|Apollo 13|Apollo 17|Neil Armstrong|Buzz Aldrin|Michael Collins (astronaut)
Yuri Gagarin|Valentina Tereshkova|Alan Shepard|John Glenn|Sally Ride|Sputnik 1|Vostok 1|NASA
European Space Agency|Roscosmos|SpaceX|International Space Station|Mir|Skylab|Tiangong space station
Hubble Space Telescope|James Webb Space Telescope|Kepler space telescope|Voyager 1|Voyager 2
Pioneer 10|New Horizons|Cassini–Huygens|Curiosity (rover)|Opportunity (rover)|Spirit (rover)
Perseverance (rover)|Saturn V|Space Shuttle|Space Shuttle Challenger|Space Shuttle Columbia
Nicolaus Copernicus|Galileo Galilei|Johannes Kepler|Edwin Hubble"""

GEOGRAPHY = """Mount Everest|K2|Kangchenjunga|Lhotse|Makalu|Annapurna|Kilimanjaro|Denali|Mont Blanc
Aconcagua|Himalayas|Andes|Alps|Rocky Mountains|Nile|Amazon River|Yangtze|Mississippi River
Danube|Ganges|Rhine|Volga River|Congo River|Lake Baikal|Lake Superior|Lake Victoria|Caspian Sea
Dead Sea|Great Lakes|Sahara|Gobi Desert|Atacama Desert|Pacific Ocean|Atlantic Ocean|Indian Ocean
Arctic Ocean|Southern Ocean|Mariana Trench|Great Barrier Reef|Amazon rainforest|Grand Canyon
Australia|Canberra|Japan|Tokyo|China|Beijing|India|New Delhi|Russia|Moscow|France|Paris
Germany|Berlin|Italy|Rome|Spain|Madrid|United Kingdom|London|Canada|Ottawa|Brazil|Brasília
Argentina|Buenos Aires|Chile|Santiago|Peru|Lima|Egypt|Cairo|Nigeria|Abuja|Kenya|Nairobi
South Africa|Pretoria|Turkey|Ankara|Indonesia|Jakarta|Thailand|Bangkok|Vietnam|Hanoi
Mexico|Mexico City|Nepal|Kathmandu|New Zealand|Wellington|Iceland|Reykjavík|Norway|Oslo
Sweden|Stockholm|Switzerland|Bern|Vatican City|Monaco"""

NOBEL = """Nobel Prize|Alfred Nobel|Nobel Prize in Physics|Nobel Prize in Chemistry|Albert Einstein
Marie Curie|Pierre Curie|Wilhelm Röntgen|Max Planck|Niels Bohr|Werner Heisenberg|Erwin Schrödinger
Paul Dirac|Enrico Fermi|Richard Feynman|Ernest Rutherford|J. J. Thomson|Linus Pauling
Dorothy Hodgkin|Frederick Sanger|Peter Higgs|Roger Penrose|Andrea Ghez|Donna Strickland
Rosalind Franklin|James Watson|Francis Crick|Alexander Fleming|Otto Hahn|Lise Meitner
Hideki Yukawa|C. V. Raman|Subrahmanyan Chandrasekhar|Abdus Salam|Murray Gell-Mann|John Bardeen
William Shockley|Maria Goeppert Mayer|Louis de Broglie|Wolfgang Pauli|Max Born|James Chadwick
Guglielmo Marconi|Robert Koch|Ivan Pavlov|Dmitri Mendeleev|Radioactivity|X-ray|Photoelectric effect
Quantum mechanics|Theory of relativity|Higgs boson|DNA|Penicillin|Transistor|Nuclear fission
Periodic table|Electron|Neutron|Proton|Atom|Laser|Black-body radiation|Isaac Newton|Charles Darwin"""

TOPICS = {"space": SPACE, "geography": GEOGRAPHY, "nobel_science": NOBEL}


def titles(block: str) -> list[str]:
    return [title.strip() for line in block.splitlines() for title in line.split("|")]


def main() -> None:
    table = pq.read_table(sys.argv[1], columns=["title", "url", "text"]).to_pylist()
    by_title = {row["title"]: row for row in table}
    output = Path(__file__).parent / "corpus.jsonl"
    written = 0
    with output.open("w") as file:
        for topic, block in TOPICS.items():
            names = titles(block)
            found = [by_title[name] for name in names if name in by_title]
            kept = [row for row in found if len(row["text"]) >= MIN_CHARS]
            missing = [name for name in names if name not in by_title]
            short = [row["title"] for row in found if len(row["text"]) < MIN_CHARS]
            print(f"{topic}: kept {len(kept)}/{len(names)}; missing {missing}; too short {short}")
            for row in kept:
                document = {
                    "topic": topic,
                    "title": row["title"],
                    "url": row["url"],
                    "text": row["text"][:MAX_CHARS],
                }
                file.write(json.dumps(document, ensure_ascii=False) + "\n")
                written += 1
    print(f"wrote {written} documents, {output.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
