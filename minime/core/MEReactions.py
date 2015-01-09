from __future__ import division

from warnings import warn
from collections import defaultdict

from six import iteritems

from cobra import Reaction


from minime.util.dogma import *
from minime.util import mu

from minime.core.Components import TranscribedGene, TranslatedGene, GenerictRNA


class MetabolicReaction(Reaction):
    """Lumped metabolic reaction with enzyme complex formation"""

    @property
    def complex_data(self):
        return self._complex_data

    @complex_data.setter
    def complex_data(self, process_data):
        self._complex_data = process_data
        process_data._parent_reactions.add(self.id)
    _complex_data = None

    @property
    def metabolic_reaction_data(self):
        return self._metabolic_reaction_data

    @metabolic_reaction_data.setter
    def metabolic_reaction_data(self, process_data):
        self._metabolic_reaction_data = process_data
        process_data._parent_reactions.add(self.id)
    _metabolic_reaction_data = None

    keff = 65.  # in per second
    reverse = False

    def update(self):
        new_stoichiometry = {}
        for component_name, value in iteritems(
                self.complex_data._stoichiometry):
            component = self._model.metabolites.get_by_id(component_name)
            new_stoichiometry[component] = -value * mu / self.keff / 3600.
        sign = -1 if self.reverse else 1
        for component_name, value in iteritems(
                self.metabolic_reaction_data._stoichiometry):
            component = self._model.metabolites.get_by_id(component_name)
            if component not in new_stoichiometry:
                new_stoichiometry[component] = 0
            new_stoichiometry[component] += value * sign
        # replace old stoichiometry with new one
        # TODO prune out old metabolites
        # doing checks for relationship every time is unnecessary. don't do.
        self.add_metabolites(new_stoichiometry,
                             combine=False, add_to_container_model=False)

        # set the bounds
        if self.reverse:
            self.lower_bound = max(
                0, -self.metabolic_reaction_data.upper_bound)
            self.upper_bound = max(
                0, -self.metabolic_reaction_data.lower_bound)
        else:
            self.lower_bound = max(0, self.metabolic_reaction_data.lower_bound)
            self.upper_bound = max(0, self.metabolic_reaction_data.upper_bound)


class TranscriptionReaction(Reaction):
    """Transcription of a TU to produced TranscribedGene"""
    _transcription_data = None

    @property
    def transcription_data(self):
        return self._transcription_data

    @transcription_data.setter
    def transcription_data(self, process_data):
        self._transcription_data = process_data
        process_data._parent_reactions.add(self.id)

    def update(self):
        protein_id = self.transcription_data.id
        new_stoichiometry = defaultdict(lambda: 0)
        TU_length = len(self.transcription_data.nucleotide_sequence)
        metabolites = self._model.metabolites
        try:
            RNAP = self._model.metabolites.get_by_id("RNA_Polymerase")
        except KeyError:
            warn("RNA Polymerase not found")
        else:
            k_RNAP = (mu * 22.7 / (mu + 0.391))*3  # 3*k_ribo
            coupling = -TU_length * mu / k_RNAP / 3600
            new_stoichiometry[RNAP] = coupling

        for transcript_id in self.transcription_data.RNA_products:
            if transcript_id not in self._model.metabolites:
                transcript = TranscribedGene(transcript_id)
                self._model.add_metabolites([transcript])
            else:
                transcript = metabolites.get_by_id(transcript_id)
            new_stoichiometry[transcript] += 1

        NT_mapping = {key: self._model.metabolites.get_by_id(value)
                      for key, value in transcription_table.iteritems()}
        for i in self.transcription_data.nucleotide_sequence:
            new_stoichiometry[NT_mapping[i]] -= 1

        new_stoichiometry[metabolites.get_by_id("ppi_c")] += TU_length - 1

        self.add_metabolites(new_stoichiometry, combine=False,
                             add_to_container_model=False)


class TranslationReaction(Reaction):
    """Translation of a TranscribedGene to a TranslatedGene"""
    _translation_data = None

    @property
    def translation_data(self):
        return self._translation_data

    @translation_data.setter
    def translation_data(self, process_data):
        self._translation_data = process_data
        process_data._parent_reactions.add(self.id)

    def update(self):
        protein_id = self.translation_data.protein
        mRNA_id = self.translation_data.mRNA
        protein_length = len(self.translation_data.amino_acid_sequence)
        metabolites = self._model.metabolites
        new_stoichiometry = defaultdict(lambda: 0)
        try:
            ribosome = self._model.metabolites.get_by_id("ribosome")
        except KeyError:
            warn("ribosome not found")
        else:
            k_ribo = mu * 22.7 / (mu + 0.391)
            coupling = -protein_length * mu / k_ribo / 3600
            new_stoichiometry[ribosome] = coupling
        try:
            transcript = metabolites.get_by_id(mRNA_id)
        except KeyError:
            warn("transcript '%s' not found" % mRNA_id)
            transcript = TranscribedGene(mRNA_id)
            self._model.add_metabolites(transcript)
        new_stoichiometry[transcript] = -1. / \
            self.translation_data.protein_per_mRNA
        try:
            protein = metabolites.get_by_id(protein_id)
        except KeyError:
            protein = TranslatedGene(protein_id)
            self._model.add_metabolites(protein)
        new_stoichiometry[protein] = 1
        aa_mapping = {key: self._model.metabolites.get_by_id(value)
                      for key, value in amino_acids.iteritems()}
        # count just the amino acids
        aa_count = defaultdict(lambda: 0)
        for i in self.translation_data.amino_acid_sequence:
            aa_count[aa_mapping[i]] -= 1
        new_stoichiometry.update(aa_count)
        # add in the tRNA's for each of the amino acids
        # We add in a "generic tRNA" for each amino acid. The production
        # of this generic tRNA represents the production of enough of any tRNA
        # (according to its keff) for that amino acid to catalyze addition
        # of a single amino acid to a growing peptide.

        for aa, count in iteritems(aa_count):
            try:
                tRNA = self._model.metabolites.get_by_id("generic_tRNA_"
                                                         + aa.id)
            except KeyError:
                warn("tRNA for '%s' not found" % aa.id)
            else:
                new_stoichiometry[tRNA] += count

        # TODO: how many protons/water molecules are exchanged when making the
        # peptide bond?
        # tRNA charging requires 2 ATP per amino acid. TODO: tRNA demand
        # Translocation and elongation each use one GTP per event
        # (len(protein) - 1). Initiation and termination also each need
        # one GTP each. Therefore, the GTP hydrolysis resulting from
        # translation is 2 * len(protein)
        new_stoichiometry[metabolites.get_by_id("h2o_c")] -= 4 * protein_length
        new_stoichiometry[metabolites.get_by_id("h_c")] += 4 * protein_length
        new_stoichiometry[metabolites.get_by_id("pi_c")] += 4 * protein_length
        new_stoichiometry[metabolites.get_by_id("gtp_c")] -= 2 * protein_length
        new_stoichiometry[metabolites.get_by_id("gdp_c")] += 2 * protein_length
        new_stoichiometry[metabolites.get_by_id("atp_c")] -= 2 * protein_length
        new_stoichiometry[metabolites.get_by_id("adp_c")] += 2 * protein_length
        self.add_metabolites(new_stoichiometry,
                             combine=False, add_to_container_model=False)


class tRNAChargingReaction(Reaction):

    tRNAData = None

    def update(self):
        stoic = {}
        data = self.tRNAData
        mets = self._model.metabolites
        complex_data = self._model.complex_data.get_by_id(data.synthetase)
        # If the generic tRNA does not exist, create it now. The meaning of
        # a generic tRNA is described in the TranslationReaction comments
        try:
            generic_tRNA = mets.get_by_id("generic_tRNA_" + data.amino_acid)
        except KeyError:
            generic_tRNA = GenerictRNA("generic_tRNA_" + data.amino_acid)
            self._model.add_metabolites([generic_tRNA])
        stoic[generic_tRNA] = 1
        self.add_metabolites(stoic)

        # compute what needs to go into production of a generic tRNA
        tRNA_amount = mu / data.tRNA_keff / 3600
        synthetase_amount = mu / data.synthetase_keff / \
            3600 * (1 + tRNA_amount)
        stoic[mets.get_by_id(data.RNA)] = -tRNA_amount
        stoic[mets.get_by_id(data.amino_acid)] = -tRNA_amount
        for component, amount in iteritems(complex_data._stoichiometry):
            stoic[mets.get_by_id(component)] = -synthetase_amount * amount
        self.add_metabolites(stoic)
