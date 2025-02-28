"""
GermaLemma -- Lemmatizer for German language text
Markus Konrad <markus.konrad@wzb.eu>, Wissenschaftszentrum Berlin für Sozialforschung
December 2019

In order to use GermaLemma, you will need to download the TIGER corpus from the University of Stuttgart
from http://www.ims.uni-stuttgart.de/forschung/ressourcen/korpora/tiger.html
Their corpus is free to use for non-commercial purposes.

It's supposed to work with a corpus that employs the STTS tagset:
http://www.ims.uni-stuttgart.de/forschung/ressourcen/lexika/TagSets/stts-table.html

Then, you should convert the corpus into pickle format for faster loading by running:

python germalemma.py tiger_release_[...].conll09

This will place a lemmata.pickle file in the "data" directory which is then automatically loaded when you use
GermaLemma like this:

```
from germalemma import GermaLemma
lemmatizer = GermaLemma()
```
"""

__title__ = 'germalemma'
__version__ = '0.1.3'

import sys
import os
import io
import codecs
import pickle
import tarfile
from collections import defaultdict
from importlib import import_module

from pyphen import Pyphen
import requests

MODULE_PATH = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEMMATA_PICKLE = os.path.join(MODULE_PATH, 'data', 'lemmata.pickle')

CORPUS_URL = "https://www.ims.uni-stuttgart.de/documents/ressourcen/korpora/tiger-corpus/download/tigercorpus-2.2.conll09.tar.gz"
CORUPS_LICENSE = os.path.join(MODULE_PATH, 'tiger_license.txt')


# valid part-of-speech prefixes
VALID_POS_PREFIXES = ('N', 'V', 'ADJ', 'ADV')

# German language adjective suffixes
ADJ_SUFFIXES_BASE = (
    'bar',
    'haft',
    'ig',
    'isch',
    'lich',
    'los',
    'sam',
    'en',
    'end',
    'ern'
)

ADJ_SUFFIXES_FLEX = (
    'e',
    'er',
    'es',
    'en',
    'em',
    'ere',
    'erer',
    'eres',
    'eren',
    'erem',
    'ste',
    'ster',
    'stes',
    'sten',
    'stem',
)

ADJ_SUFFIXES_DICT = {}

for suffix in ADJ_SUFFIXES_BASE:
    for flex in ADJ_SUFFIXES_FLEX:
        ADJ_SUFFIXES_DICT[suffix + flex] = suffix


class GermaLemma(object):
    """
    Lemmatizer for German language text main class.
    """
    pyphen_dic = Pyphen(lang='de')

    def __init__(self, **kwargs):
        """
        Initialize GermaLemma lemmatizer. By default, it will load the lemmatizer data from 'data/lemmata.pickle'. You
        can also pass a manual lemmata dictionary via `lemmata` or load a corpus in CONLL09 format via `tiger_corpus`
        or load pickled lemmatizer data from `pickle`.
        Force usage of pattern.de module by setting `use_pattern_module` to True (or False for not using). By default,
        it will try to use pattern.de if it is installed.
        """
        if 'lemmata' in kwargs:
            self.lemmata = kwargs['lemmata']
            if 'lemmata_lower' in kwargs:
                self.lemmata_lower = kwargs['lemmata_lower']
            else:
                self.lemmata_lower = {pos: {token.lower(): lemma for token, lemma in pos_lemmata}
                                      for pos, pos_lemmata in self.lemmata.items()}
        elif 'tiger_corpus' in kwargs:
            self.lemmata, self.lemmata_lower = self.load_corpus_lemmata(kwargs['tiger_corpus'])
        elif 'pickle' in kwargs:
            self.load_from_pickle(kwargs['pickle'])
        else:
            try:
                self.load_from_pickle(DEFAULT_LEMMATA_PICKLE)
            except FileNotFoundError:
                self.load_from_pickle(os.path.join(sys.prefix, DEFAULT_LEMMATA_PICKLE))

        self.pattern_module = None
        use_pattern_module = kwargs.get('use_pattern_module', None)
        if use_pattern_module in (True, None):
            try:
                self.pattern_module = import_module('pattern.de')
            except ImportError:
                if use_pattern_module is True:
                    raise ImportError('pattern.de module could not be loaded')

    def find_lemma(self, w, pos_tag):
        """
        Find a lemma for word `w` that has a Part-of-Speech tag `pos_tag`. `pos_tag` should be a valid STTS tagset tag
        (see http://www.ims.uni-stuttgart.de/forschung/ressourcen/lexika/TagSets/stts-table.html) or a simplified form
        with:
        - 'N' for nouns
        - 'V' for verbs
        - 'ADJ' for adjectives
        - 'ADV' for adverbs
        All other tags will raise a ValueError("Unsupported POS tag")!
        Return the lemma or, if no lemma was found, return `w`.
        """
        if not w:   # do not process empty strings
            return w

        if pos_tag == 'NE':   # if word is a name, it already is the lemma
            return w

        if pos_tag.startswith('N') or pos_tag.startswith('V'):
            pos = pos_tag[0]
        elif pos_tag.startswith('ADJ') or pos_tag.startswith('ADV'):
            pos = pos_tag[:3]
        else:
            raise ValueError("Unsupported POS tag")

        # look if we can directly find `w` in the lemmata dictionary
        res = self.dict_search(w, pos)

        if not res and self.pattern_module:   # try to use pattern.de module
            res_pattern = self._lemma_via_patternlib(w, pos)
            if res_pattern != w:
                res = res_pattern

        if not res:
            # try to split nouns that are made of composita
            if pos == 'N':
                res = self._composita_lemma(w) or w
            else:
                res = w

            # try to lemmatize adjectives using prevalent German language adjective suffixes
            if pos == 'ADJ':
                res = self._adj_lemma(res)

        # nouns always start with a capital letter
        if pos == 'N':
            if len(res) > 1 and res[0].islower():
                res = res[0].upper() + res[1:]
        else:  # all other forms are lower-case
            res = res.lower()

        return res

    def dict_search(self, w, pos, use_lower=False):
        """
        Lemmata dictionary lookup for word `w` with POS tag `pos`.
        Return lemma if found, else None.
        """
        pos_lemmata = self.lemmata_lower[pos] if use_lower else self.lemmata[pos]

        return pos_lemmata.get(w, None)

    def _adj_lemma(self, w):
        """
        Try to lemmatize adjectives using prevalent German language adjective suffixes. Return possibly lemmatized
        adjective.
        """
        for full, reduced in ADJ_SUFFIXES_DICT.items():
            if w.endswith(full):
                return w[:-len(full)] + reduced

        return w

    def _composita_lemma(self, w):
        """
        Try to split a word `w` that is possibly made of composita.
        Return the lemma if found, else return None.
        """

        # find most important split position first when a hyphen is used in the word
        try:
            split_positions = [w.rfind('-') + 1]
        except ValueError:
            split_positions = []

        # add possible split possitions by using Pyphen's hyphenation positions
        split_positions.extend([p for p in self.pyphen_dic.positions(w) if p not in split_positions])

        # now split `w` by hyphenation step by step
        for hy_pos in split_positions:
            # split in left and right parts (start and end of the strings)
            left, right = w[:hy_pos], w[hy_pos:]

            # look if the right part can be found in the lemmata dictionary
            # if we have a noun, a lower case match will also be accepted
            if left and right and not right.endswith('innen'):
                res = self.dict_search(right, 'N', use_lower=right[0].islower())
                if res:
                    # concatenate the left side with the found partial lemma
                    if left[-1] == '-':
                        res = left + res.capitalize()
                    else:
                        res = left + res.lower()

                    if w.isupper():
                        return res.upper()
                    else:
                        return res

        return None

    def _lemma_via_patternlib(self, w, pos):
        """
        Try to find a lemma for word `w` that has a Part-of-Speech tag `pos_tag` by using pattern.de module's functions.
        Return the lemma or `w` if lemmatization was not possible with pattern.de
        """
        if not self.pattern_module:
            raise RuntimeError('pattern.de module not loaded')

        if pos == 'NP':  # singularize noun
            return self.pattern_module.singularize(w)
        elif pos.startswith('V'):  # get infinitive of verb
            return self.pattern_module.conjugate(w)
        elif pos.startswith('ADJ') or pos.startswith('ADV'):  # get baseform of adjective or adverb
            return self.pattern_module.predicative(w)

        return w

    @classmethod
    def load_corpus_lemmata(cls, corpus_file):
        lemmata = defaultdict(dict)
        lemmata_lower = defaultdict(dict)

        with codecs.open(corpus_file, encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) == 15:
                    token, lemma = parts[1:3]
                    pos = parts[4]
                    cls.add_to_lemmata_dicts(lemmata, lemmata_lower, token, lemma, pos)

        return lemmata, lemmata_lower
    
    @classmethod
    def download_tiger(cls,
                       url=CORPUS_URL):
        # Check if already downloaded
        if os.path.exists(DEFAULT_LEMMATA_PICKLE):
            print(f'TIGER Corpus already downloaded, at loaction {DEFAULT_LEMMATA_PICKLE}')
            choice = input('Download the corpus again? Enter: yes or no\n')
            if choice.lower() != 'yes':
                print('Aborting...')
                return None
        # License Agreement
        print('The TIGER Corpus is not part of this package and has a License agreement for academic purposes')
        choice = input('Do you want to read the License? Enter: yes or no (abort)\n')
        if choice.lower() != 'yes':
            print('You have to read the license, before proceeding. Aborting...')
            return None
        
        with open(CORUPS_LICENSE) as f:
            license_text = f.read()

        print()
        print('-'*80)
        print(license_text)
        print('-'*80)
        print()
        choice = input('Do you agree with above terms and conditions? Enter: yes or no (abort)\n')
        if choice.lower() != 'yes':
            print('Aborting...')
            return None
        
        print('Downloading corpus files...')
        # download
        r = requests.get(url, stream=True)
        corpus_buffer = io.BytesIO(r.content)
        tar = tarfile.open(fileobj=corpus_buffer, mode='r:gz')
        corpus = tar.extractfile(tar.members[0])

        print('Success!')
        print('Parsing corpus...')

        lemmata, lemmata_lower = cls.load_tiger_tar(corpus)

        print('Success!')
        print('Writing corpus to local pickle file...')
        with open(DEFAULT_LEMMATA_PICKLE, 'wb') as f:
            pickle.dump((lemmata, lemmata_lower), f, protocol=2)

        print('Success!')

        print('You can now use the lemmatizer: lemmatizer = GermaLemma()')


    @classmethod
    def load_tiger_tar(cls, corpus):
        lemmata = defaultdict(dict)
        lemmata_lower = defaultdict(dict)

        for line in corpus.readlines():
            parts = line.decode('utf-8').split()
            if len(parts) == 15:
                token, lemma = parts[1:3]
                pos = parts[4]
                cls.add_to_lemmata_dicts(lemmata, lemmata_lower, token, lemma, pos)

        return lemmata, lemmata_lower
    

    @staticmethod
    def add_to_lemmata_dicts(lemmata, lemmata_lower, token, lemma, pos):
        for pos_prefix in VALID_POS_PREFIXES:
            if pos.startswith(pos_prefix):
                if token not in lemmata[pos_prefix]:
                    lemmata[pos_prefix][token] = lemma
                if lemma not in lemmata[pos_prefix]:  # for quicker lookup
                    lemmata[pos_prefix][lemma] = lemma

                if pos_prefix == 'N':
                    token_lower = token.lower()
                    if token_lower not in lemmata_lower[pos_prefix]:
                        lemmata_lower[pos_prefix][token_lower] = lemma
                    lemma_lower = lemma.lower()
                    if lemma_lower not in lemmata_lower[pos_prefix]:
                        lemmata_lower[pos_prefix][lemma_lower] = lemma

                return

    def save_to_pickle(self, pickle_file):
        with open(pickle_file, 'wb') as f:
            pickle.dump((self.lemmata, self.lemmata_lower), f, protocol=2)

    def load_from_pickle(self, pickle_file):
        with open(pickle_file, 'rb') as f:
            self.lemmata, self.lemmata_lower = pickle.load(f)


if __name__ == '__main__':
    # script entry point to convert a CONLL09 TIGER corpus file into a Python pickle file (which only contains the
    # lemmata and is much faster to load)

    import sys
    if len(sys.argv) < 2:
        print('run as: %s <path to TIGER corpus conll09 file>' % sys.argv[0])
        exit(1)

    corpus_file = sys.argv[1]
    print("loading corpus file '%s'..." % corpus_file)
    lemmatizer = GermaLemma(tiger_corpus=corpus_file, use_pattern_module=False)

    pickle_file = DEFAULT_LEMMATA_PICKLE
    print("saving as pickle file '%s'" % pickle_file)
    lemmatizer.save_to_pickle(pickle_file)

    print("done.")
    exit(0)
