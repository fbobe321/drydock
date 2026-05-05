from rdkit import Chem

smiles = "CC12COC(OC1)(OC2)C1=CC=CC=C1"
mol = Chem.MolFromSmiles(smiles)
if mol:
    print("SMILES parsed successfully.")
    print(f"Formula: {mol.GetFormula()}")
    print(f"Molar Mass: {Chem.rdMolDescriptors.ExactMolWt(mol)}")
else:
    print("Failed to parse SMILES.")
