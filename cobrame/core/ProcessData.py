from collections import defaultdict

from cobrame.util.dogma import *
from cobrame.util.mass import *
from cobrame.util import mu
from cobrame.core.MEReactions import *


class ProcessData(object):
    """Generic class for holding information about a process

    ME reactions are built from information in these objects

    """

    def __init__(self, id, model):
        self.id = id
        self._model = model
        # parents need to be updated every time the process is updated
        # a parent must have an update method
        self._parent_reactions = set()
        model.process_data.append(self)

    @property
    def model(self):
        return self._model

    @property
    def parent_reactions(self):
        return {self._model.reactions.get_by_id(i)
                for i in self._parent_reactions}

    def _update_parent_reactions(self):
        reactions = self._model.reactions
        for i in self._parent_reactions:
            reactions.get_by_id(i).update()

    def __repr__(self):
        return "<%s %s at 0x%x>" % (self.__class__.__name__, self.id, id(self))


class StoichiometricData(ProcessData):
    """Encodes the stoichiometry for a metabolic reaction.

    This stoichiometry and ID will often link directly to M-model reactions

    :param dict _stoichiometry:
        Dictionary of {metabolite_id: stoichiometry}

    :param defaultdict subreactions:
        This should rarely contain anything. Only in special cases where
        a second diluted enzyme needs added to metabolic reaction
    """
    def __init__(self, id, model):
        ProcessData.__init__(self, id, model)
        model.stoichiometric_data.append(self)
        self._stoichiometry = {}
        self.subreactions = defaultdict(int)

    @property
    def stoichiometry(self):
        return self._stoichiometry


class ModificationData(ProcessData):
    def __init__(self, id, model):
        ProcessData.__init__(self, id, model)
        model.modification_data.append(self)
        self.stoichiometry = {}
        self.enzyme = None
        self.keff = 65.

    def get_complex_data(self):
        for i in self._model.complex_data:
            if self.id in i.modifications:
                yield i


class SubreactionData(ProcessData):
    def __init__(self, id, model):
        ProcessData.__init__(self, id, model)
        model.subreaction_data.append(self)
        self.stoichiometry = {}
        self.enzyme = None
        self.keff = 65.

    def get_complex_data(self):
        for i in self._model.complex_data:
            if self.id in i.modifications:
                yield i


class ComplexData(ProcessData):

    """Contains all information associated with the formation of an
    enzyme complex

    :param dict stoichiometry:
        Dictionary containing {protein_id: count} for all protein subunits
        comprising enzyme complex

    :param dict modifications:
        Dictionary of {modfication_data_id: count} for all protein
        modifications. This can include cofactor/prosthetic group binding
        or enzyme side group addition.

    """

    @property
    def formation(self):
        """a read-only link to the formation reaction object"""
        try:
            return self._model.reactions.get_by_id("formation_" + self.id)
        except KeyError:
            return None

    @property
    def complex(self):
        """a read-only link to the complex object"""
        return self._model.metabolites.get_by_id(self.complex_id)

    @property
    def complex_id(self):
        """There are cases where multiple equivalent processes can result in
        the same final complex. This allows the equivalent final complex
        complex_id to be queried. This only needs set in the above case"""
        return self.id if self._complex_id is None else self._complex_id

    @complex_id.setter
    def complex_id(self, value):
        self._complex_id = None if value == self.id else value

    def __init__(self, id, model):
        ProcessData.__init__(self, id, model)
        model.complex_data.append(self)
        # {Component.id: stoichiometry}
        self.stoichiometry = defaultdict(float)
        self.chaperones = {}
        # {ModificationData.id : number}
        self.modifications = {}
        self.unmodified_complex_id = ''  # TODO confirm this is unused
        self._complex_id = None  # assumed to be the same as id if None

    def create_complex_formation(self, verbose=True):
        """creates a complex formation reaction

        This assumes none exists already. Will create a reaction (prefixed by
        "formation_") which forms the complex"""
        formation_id = "formation_" + self.id
        if formation_id in self._model.reactions:
            raise ValueError("reaction %s already in model" % formation_id)
        formation = ComplexFormation(formation_id)
        formation.complex_data_id = self.id
        formation._complex_id = self.complex_id
        self._model.add_reaction(formation)
        formation.update(verbose=verbose)


class TranscriptionData(ProcessData):
    def __init__(self, id, model, RNA_products=set()):
        ProcessData.__init__(self, id, model)
        model.transcription_data.append(self)
        self.nucleotide_sequence = ''
        self.RNA_products = RNA_products
        self.RNA_polymerase = ''
        self.rho_dependent = False
        # {ModificationData.id : number}
        self.modifications = defaultdict(int)
        self.subreactions = defaultdict(int)

    @property
    def nucleotide_count(self):
        return {transcription_table[i]: self.nucleotide_sequence.count(i)
                for i in ["A", "T", "G", "C"]}

    @property
    def RNA_types(self):
        return (self._model.metabolites.get_by_id(i).RNA_type for i in
                self.RNA_products)

    @property
    def mass(self):
        return compute_RNA_mass(self.nucleotide_sequence, self.excised_bases)

    # Bases to be excised due to splicing tRNA, rRNA, or ncRNA in RNA_products
    # i.e. {"amp_c": 10, "gmp_c": 11, "ump_c": 9, "cmp_c": 11}
    @property
    def excised_bases(self):
        # Skip if TU does not have any annotated RNA Products
        if len(self.RNA_products) == 0:
            return {}

        # Skip if TU only codes for mRNA
        RNA_types = set(self.RNA_types)
        if RNA_types == {"mRNA"}:
            return {}

        # Get dictionary of all nucleotide counts for TU
        seq = self.nucleotide_sequence
        counts = {i: seq.count(i) for i in ("A", "T", "G", "C")}

        # Subtract bases contained in RNA_product from dictionary
        metabolites = self._model.metabolites
        for product_id in self.RNA_products:
            gene_seq = metabolites.get_by_id(product_id).nucleotide_sequence
            for b in ("A", "T", "G", "C"):
                counts[b] -= gene_seq.count(b)

        # First base being a triphosphate will be handled by the reaction
        # producing an extra ppi during transcription. But generally, we add
        # triphosphate bases when transcribing, but excise monophosphate bases.
        monophosphate_counts = {dogma.transcription_table[k].replace("tp_c",
                                                                     "mp_c"): v
                                for k, v in iteritems(counts)}

        return monophosphate_counts

    @property
    def codes_stable_rna(self):
        # Return true if a stable RNA is in RNA_products
        has_stable_rna = False
        for RNA in self.RNA_products:
            try:
                gene = self._model.metabolites.get_by_id(RNA)
            except KeyError:
                pass
            else:
                if gene.RNA_type in ['tRNA', 'rRNA', 'ncRNA']:
                    has_stable_rna = True
        return has_stable_rna


class GenericData(ProcessData):
    def __init__(self, id, model, component_list):
        if not id.startswith("generic_"):
            warn("best practice for generic id to start with generic_")
        ProcessData.__init__(self, id, model)
        model.generic_data.append(self)
        self.component_list = component_list

    def create_reactions(self):
        model = self._model
        try:
            generic_metabolite = model.metabolites.get_by_id(self.id)
        except KeyError:
            generic_metabolite = GenericComponent(self.id)
            model.add_metabolites([generic_metabolite])
       
    
        #takes care of generic_Dus and generic_16Sm4Cm1402
        if type(self.component_list) == dict:
            stoic = {}
            reaction_id = "formation_" + str(generic_metabolite.id)
            try:
                reaction = model.reactions.get_by_id(reaction_id)
            except KeyError:
                reaction = GenericFormationReaction(reaction_id)
                model.add_reaction(reaction)
                
            for c_id in self.component_list:
                stoic.update({model.metabolites.get_by_id(c_id): -1 * self.component_list[c_id]})
            stoic.update({generic_metabolite : 1})
            reaction.add_metabolites(stoic, combine=False)
                
            
        #normal function of generic components
        else:
            for c_id in self.component_list:
                reaction_id = c_id + "_to_" + self.id
                try:
                    reaction = model.reactions.get_by_id(reaction_id)
                except KeyError:
                    reaction = GenericFormationReaction(reaction_id)
                    model.add_reaction(reaction)
                stoic = {generic_metabolite: 1,
                         model.metabolites.get_by_id(c_id): -1}
                reaction.add_metabolites(stoic, combine=False)


class TranslationData(ProcessData):
    _amino_acid_sequence = ""
    mRNA = None
    nucleotide_sequence = ""
    term_enzyme = None

    def __init__(self, id, model, mRNA, protein):
        ProcessData.__init__(self, id, model)
        model.translation_data.append(self)
        self.mRNA = mRNA
        self.protein = protein
        self.subreactions = defaultdict(int)
        self.modifications = defaultdict(int)

    @property
    def amino_acid_sequence(self):
        codons = (self.nucleotide_sequence[i: i + 3]
                  for i in range(0, (len(self.nucleotide_sequence)), 3))
        amino_acid_sequence = ''.join(codon_table.get(i, "K") for i in codons)
        amino_acid_sequence = amino_acid_sequence.rstrip("*")
        if not amino_acid_sequence.startswith('M'):
            start_codons = self._model.global_info["met_start_codons"]
            if self.first_codon not in start_codons:
                warn("%s starts with '%s' which is not a start codon" %
                     (self.mRNA, self.first_codon))
            amino_acid_sequence = 'M' + ''.join(amino_acid_sequence[1:])
        if "*" in amino_acid_sequence:
            amino_acid_sequence = amino_acid_sequence.replace('*', 'K')
        return amino_acid_sequence

    @property
    def last_codon(self):
        return self.nucleotide_sequence[-3:].replace('T', 'U')

    @property
    def first_codon(self):
        return self.nucleotide_sequence[:3].replace('T', 'U')

    def _itercodons(self):
        None
        yield "AAU"

    @property
    def codon_count(self):
        # exclude the last three stop codons from count
        codons = (self.nucleotide_sequence[i: i + 3]
                  for i in range(0, (len(self.nucleotide_sequence)-3), 3))
        codon_count = defaultdict(int)
        for i in codons:
            codon_count[i.replace('T', 'U')] += 1

        # Remove one methionine (AUG) from codon count to account for start
        first_codon = self.first_codon
        if first_codon in self._model.global_info["met_start_codons"]:
            codon_count[first_codon] -= 1
        else:
            warn("%s starts with '%s' which is not a start codon" %
                 (self.mRNA, first_codon))
        return codon_count

    @property
    def amino_acid_count(self):
        """count of each amino acid in the protein"""
        aa_count = defaultdict(int)
        for i in self.amino_acid_sequence:
            aa_count[amino_acids[i]] += 1
        return aa_count

    @property
    def mass(self):
        """mass in kDa"""
        return compute_protein_mass(self.amino_acid_count)

    @property
    def elongation_subreactions(self):
        global_info = self._model.global_info
        elongation_subreactions = {}
        # Add the subreaction_data associated with each tRNA/AA addition
        for codon, count in self.codon_count.items():
            codon = codon.replace('U', 'T')
            if codon == 'TGA':
                print 'Adding selenocystein for %s' % self.id
                aa = 'sec'
                # TODO account of selenocysteine in formula
            else:
                abbreviated_aa = dogma.codon_table[codon]
                if abbreviated_aa == "*":
                    break
                # Filter out the compartment and stereochemistry from aa id
                aa = dogma.amino_acids[abbreviated_aa].split('_')[0]
            codon = codon.replace('T', 'U')
            subreaction_id = aa + '_addition_at_' + codon
            try:
                self._model.subreaction_data.get_by_id(subreaction_id)
            except KeyError:
                warn('subreaction %s not in model' % subreaction_id)
            else:
                elongation_subreactions[subreaction_id] = count

        for subreaction_id in global_info['translation_elongation_subreactions']:
            try:
                self._model.subreaction_data.get_by_id(subreaction_id)
            except KeyError:
                warn('subreaction %s not in model' % subreaction_id)
            else:
                # No elongation subreactions needed for start codon
                elongation_subreactions[subreaction_id] = len(self.amino_acid_sequence) - 1.

        return elongation_subreactions

    @property
    def translation_start_subreactions(self):
        # Read-only link to list of start subreactions for organism
        return self._model.global_info.get('translation_start_subreactions')

    @property
    def translation_termination_subreactions(self):
        termination_subreactions = []
        all_subreactions = self._model.subreaction_data
        global_info = self._model.global_info
        last_codon = self.last_codon
        term_enzyme = global_info["translation_terminators"].get(last_codon)
        if term_enzyme is not None:
            termination_subreaction_id = last_codon + '_' + term_enzyme + \
                                         '_mediated_termination'
            try:
                all_subreactions.get_by_id(termination_subreaction_id)
            except KeyError:
                warn("Termination subreaction '%s' not found" %
                     (termination_subreaction_id))
            else:
                termination_subreactions.append(termination_subreaction_id)
        return termination_subreactions


class tRNAData(ProcessData):
    synthetase = None
    synthetase_keff = 65.

    def __init__(self, id, model, amino_acid, RNA, codon):
        ProcessData.__init__(self, id, model)
        model.tRNA_data.append(self)
        self.codon = codon
        self.amino_acid = amino_acid
        self.RNA = RNA
        self.modifications = defaultdict(int)

        for bnum in trna_bnum_anticodon_start_position_dict.keys():
            if bnum in self.id:
                self.anticodon = trna_bnum_anticodon_start_position_dict[bnum].get('anticodon')
                self.start_position = trna_bnum_anticodon_start_position_dict[bnum].get('start_position')
                if self.start_position == 1:
                    self.trna_sequence = '_' + model.metabolites.get_by_id('RNA_' + bnum).nucleotide_sequence
                elif self.start_position == 0:
                    self.trna_sequence = model.metabolites.get_by_id('RNA_' + bnum).nucleotide_sequence

        #find codon/ conserved region 21-45
        codon_start = self.trna_sequence.find(self.anticodon, 33)
        if 'lys' in self.amino_acid:
            codon_start = codon_start + 1

        codon_end = codon_start + 3
        conserved_region_start = codon_start - 13   #upstream conserved region 21-33
        conserved_region_end = codon_end + 9
        checker = self.trna_sequence[conserved_region_start : conserved_region_end]



        #region 0-16, 21-45, 48-76
        trna_sequence_position_0_to_16 = self.trna_sequence[0:17]
        trna_sequence_position_21_to_45 = self.trna_sequence[conserved_region_start : conserved_region_end]
        trna_sequence_position_48_to_76 = self.trna_sequence[-29:]

        self.trna_sequence_position_0_to_16 = trna_sequence_position_0_to_16
        self.trna_sequence_position_21_to_45 = trna_sequence_position_21_to_45
        self.trna_sequence_position_48_to_76 = trna_sequence_position_48_to_76

        #D-loop
        D_loop_first_split = self.trna_sequence.split(self.trna_sequence_position_0_to_16)
        D_loop_second_split = D_loop_first_split[1].split(self.trna_sequence_position_21_to_45)[0]

        #adding spaces in D-loop
        #checker
        if 'GG' not in  D_loop_second_split:
            print 'ERROR: NO GG SEQUENCE IN D-LOOP', self.id
        D_loop_split = D_loop_second_split.split('GG')

        #Fills In positions 17,17a   (temp = 17,17a,18,19)
        if len(D_loop_split[0]) == 0 :
            temp_fill_D_loop = 'xx' + 'GG'
        elif len(D_loop_split[0]) == 1 :
            temp_fill_D_loop = D_loop_split[0] +'x' +'GG'
        else:
            temp_fill_D_loop = D_loop_split[0] + 'GG'

        #fill in positions 20, 20a, 20b
        if len(D_loop_split[1]) == 0 :
            print 'error: position 20 must exist'

        elif len(D_loop_split[1]) == 1 :
            temp_fill_D_loop = temp_fill_D_loop + D_loop_split[1] +'xx'
        elif len(D_loop_split[1]) == 2 :
            temp_fill_D_loop = temp_fill_D_loop + D_loop_split[1] +'x'
        elif len(D_loop_split[1]) == 3 :
            temp_fill_D_loop = temp_fill_D_loop + D_loop_split[1]
        else:
            print 'error: too many in position 20,20a,20b'
        self.trna_sequence_position_17_20_Dloop = temp_fill_D_loop


        #Var-loop
        Var_loop_first_split = self.trna_sequence.split(self.trna_sequence_position_48_to_76)
        Var_loop_second_split = Var_loop_first_split[0].split(self.trna_sequence_position_21_to_45)[1]
        self.trna_sequence_position_46_47_Vloop = Var_loop_second_split

class TranslocationData(ProcessData):
    """

    """

    def __init__(self, id, model):
        ProcessData.__init__(self, id, model)
        self.keff = 65.
        # enzyme_dict = {enzyme_id: {length_dependent: <True or False>,
        #                            fixed_keff: <True or False>}}
        self.enzyme_dict = {}
        self.length_dependent_energy = False
        self.stoichiometry = {}
        model.translocation_data.append(self)


class PostTranslationData(ProcessData):
    """
    PostTranslationData id can be anything, but the preprocessed protein id
    and processed protein id must be defined
    """

    def __init__(self, id, model, processed_protein, preprocessed_protein):
        ProcessData.__init__(self, id, model)
        self.processed_protein_id = processed_protein
        self.unprocessed_protein_id = preprocessed_protein
        self.translocation = defaultdict(float)

        self.folding_mechanism = ''
        self.aggregation_propensity = 0.
        # Dictionaries of {str(temperature): value}
        self.keq_folding = {}
        self.k_folding = {}
        # some small peptides are more likely to be folded by certain
        # chaperones. This is accounted for using propensity_scaling.
        self.propensity_scaling = 1.

        self.modifications = defaultdict(float)
        self.subreactions = defaultdict(float)
        self.surface_area = {}
        model.posttranslation_data.append(self)
