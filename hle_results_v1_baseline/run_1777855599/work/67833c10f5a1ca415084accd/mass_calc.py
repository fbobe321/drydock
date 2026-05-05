def molar_mass(formula_dict):
    masses = {
        'C': 12.011,
        'H': 1.008,
        'O': 15.999,
        'Cl': 35.45
    }
    total = 0
    for atom, count in formula_dict.items():
        total += masses[atom] * count
    return total

# CC12COC(OC1)(OC2)C1=CC=CC=C1
# C: 1 (CC1) + 1 (C1) + 1 (C) + 1 (O) + 1 (C1) + 1 (O) + 1 (C2) + 1 (C1) + 6 (C1=CC=CC=C1)
# Wait, let's re-parse carefully.
# CC12COC(OC1)(OC2)C1=CC=CC=C1
# Structure:
# C (methyl)
# C1 (part of epoxide/ring)
# 2 (epoxide oxygen? No, CC12... usually means a spiro epoxide or similar)
# Let's re-examine the SMILES: CC12COC(OC1)(OC2)C1=CC=CC=C1
# C (1)
# C1 (2)
# 2 (epoxide oxygen? No, 1 and 2 are ring closures)
# C (3)
# O (4)
# C (5)
# (
#   O (6)
#   C1 (7)
# )
# (
#   O (8)
#   C2 (9)
# )
# C1 (10)
# =
# C (11)
# =
# C (12)
# -
# C (13)
# =
# C (14)
# -
# C (15)
# =
# C (16)
# -
# C (17)

# Let's count atoms:
# C: 1 (methyl) + 1 (C1) + 1 (C) + 1 (C1) + 1 (C2) + 1 (C1) + 6 (benzene ring) = 12 carbons?
# Let's re-read: CC12COC(OC1)(OC2)C1=CC=CC=C1
# C (1)
# C1 (2)
# 2 (epoxide oxygen? No, 1 and 2 are ring closures)
# C (3)
# O (4)
# C (5)
# (
#   O (6)
#   C1 (7)
# )
# (
#   O (8)
#   C2 (9)
# )
# C1 (10)
# =
# C (11)
# =
# C (12)
# -
# C (13)
# =
# C (14)
# -
# C (15)
# =
# C (16)
# -
# C (17)

# Let's use a different approach.
# CC12COC(OC1)(OC2)C1=CC=CC=C1
# This looks like a spiro epoxide.
# C (methyl)
# C (spiro carbon 1)
# C (spiro carbon 2)
# O (epoxide oxygen 1)
# C (epoxide carbon 1)
# O (epoxide oxygen 2)
# C (epoxide carbon 2)
# C (benzene ring carbon 1)
# ...
# Let's count atoms by hand:
# C: 1 (methyl) + 1 (spiro C) + 1 (spiro C) + 1 (C in ring) + 1 (C in ring) + 1 (C in ring) + 1 (C in ring) + 1 (C in ring) + 1 (C in ring) + 1 (C in ring)
# Wait, CC12COC(OC1)(OC2)C1=CC=CC=C1
# C (1)
# C (2) - this is C1
# 2 - this is epoxide oxygen? No, 2 is a ring closure.
# C (3)
# O (4)
# C (5) - this is C1
# (
#   O (6)
#   C (7) - this is C2
# )
# (
#   O (8)
#   C (9) - this is C1
# )
# C (10) - this is C1
# =
# C (11)
# =
# C (12)
# -
# C (13)
# =
# C (14)
# -
# C (15)
# =
# C (16)
# -
# C (17)

# Let's try to draw it:
# C1 is connected to C2, C5, C9, C10.
# C2 is connected to C1, C7.
# C1 is connected to C2, C5, C9, C10.
# This is getting confusing. Let's use a search for the SMILES.
