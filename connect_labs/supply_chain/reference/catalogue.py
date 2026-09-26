"""A starting catalogue for a community management of acute malnutrition
programme, and the rule about what is in it.

A programme that only ever buys RUTF does not exist. The same store holds the
supplementary food for moderate cases, the therapeutic milks for the
stabilisation phase, the rehydration salts, the tape the diagnosis is made
with and the antibiotic that goes with every admission -- and every one of
them is a thing that gets quoted, shipped, counted and run out of. A catalogue
with one row in it cannot show any of that.

**What is asserted here and what is not.** Every figure below is a structural
fact about the standard pack -- how many sachets are in the carton, what one
weighs -- which is what the unit ladder needs and what a price is divided by.
Identifiers that belong to somebody else are NOT invented:

  - `unicef_material_number` is set only for the two products whose Supply
    Division number is unambiguous and widely published. Everywhere else it is
    blank, because a plausible-looking material number is worse than none: it
    would be copied into an order.
  - No GTIN appears anywhere. A GTIN is a company's own key; fabricating one
    that passes its check digit would produce an identifier that may belong to
    a real, different product.
  - `sku` is OUR catalogue key for a manufacturer's product, not their part
    number. That is what a SKU is here -- `Item.sku` is unique per scope and
    nothing claims it came from the manufacturer.

`spec_reference` is left blank wherever a precise citation is not to hand,
rather than filled with an approximate one. The page renders "no reference
recorded", which is the true state.

Shelf life sits at two levels on purpose. The product carries the MINIMUM the
programme will accept; the trade item carries what the manufacturer states.
That is the pair a compliance check compares.
"""

# One entry per product, keyed by `slug`. Seeding SKIPS a slug that is already
# in the catalogue rather than updating it -- see `catalogue_seed`.
#
# F-75's sachet is 102.5 g and `base_unit_grams` is an integer column, so it
# is left unset rather than rounded to 102 -- a per-gram figure derived from a
# rounded weight is wrong by half a percent and nothing downstream would say
# so. The catalogue shows "not stated", which is accurate.
PRODUCTS = (
    # RUTF leads because it is what a CMAM programme is built around -- and
    # because a trade item below names it. Seeding is skip-if-present, so a
    # programme that already has RUTF (imported from its own tracker, perhaps
    # carrying a ration table somebody set) keeps its own row untouched; a
    # fresh one gets this. Without it the Plumpy'Nut item had no product to
    # hang off and was silently refused, so the seed was incomplete on the one
    # product every such programme needs.
    {
        "slug": "rutf",
        "name": "Ready-to-use therapeutic food",
        "category": "therapeutic_food",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 150,
        "base_unit_grams": 92,
        "shelf_life_months_minimum": 18,
        "spec_reference": "WHO/WFP/UNICEF/UN-SCN joint statement, 2007",
        "unicef_material_number": "S0000240",
        "spec_requirements": [
            {
                "field": "shelf_life_months",
                "operator": ">=",
                "value": 18,
                "unit": "months",
                "rationale": "Sea freight and clearance routinely take four months of it.",
            }
        ],
    },
    {
        "slug": "rusf",
        "name": "Ready-to-use supplementary food",
        "category": "supplementary_food",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 150,
        "base_unit_grams": 92,
        "shelf_life_months_minimum": 18,
        "spec_requirements": [
            {
                "field": "shelf_life_months",
                "operator": ">=",
                "value": 18,
                "unit": "months",
                "rationale": "Sea freight and clearance routinely take four months of it.",
            }
        ],
    },
    {
        "slug": "f75",
        "name": "Therapeutic milk F-75 (stabilisation)",
        "category": "therapeutic_food",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 120,
        "shelf_life_months_minimum": 18,
    },
    {
        "slug": "f100",
        "name": "Therapeutic milk F-100 (rehabilitation)",
        "category": "therapeutic_food",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 90,
        "base_unit_grams": 114,
        "shelf_life_months_minimum": 18,
    },
    {
        "slug": "resomal",
        "name": "ReSoMal rehydration solution for malnutrition",
        "category": "oral_rehydration",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 100,
        "base_unit_grams": 42,
        "shelf_life_months_minimum": 18,
    },
    {
        "slug": "muac-tape-child",
        "name": "MUAC tape, child (11.5 cm)",
        "category": "diagnostic",
        "base_unit": "tape",
        "pack_unit": "pack",
        "base_per_pack": 50,
        # No course, no shelf life: a tape is not dispensed over days and does
        # not expire. The catalogue asks for neither, which is the whole
        # reason `category` decides whether a ration table is missing.
        "unicef_material_number": "S0145620",
    },
    {
        "slug": "amoxicillin-dt-250",
        "name": "Amoxicillin dispersible tablets, 250 mg",
        "category": "antibiotic",
        "base_unit": "tablet",
        "pack_unit": "box",
        "base_per_pack": 100,
        "shelf_life_months_minimum": 24,
    },
    {
        "slug": "scale-infant",
        "name": "Infant weighing scale, 25 kg x 10 g",
        "category": "equipment",
        "base_unit": "scale",
        "pack_unit": "unit",
        "base_per_pack": 1,
    },
    {
        "slug": "height-board",
        "name": "Height and length measuring board, child",
        "category": "equipment",
        "base_unit": "board",
        "pack_unit": "unit",
        "base_per_pack": 1,
    },
)

# Trade items: one manufacturer's version of a product, which is the only
# level that can be ordered. Deliberately few. Each is a standard pack from a
# manufacturer whose configuration is published; where a programme's own
# suppliers have not yet stated a pack specification, no trade item is
# invented for them -- that gap is exactly what the quote pages are asking
# them to close, and filling it in here with a guess would answer the question
# with our own invention.
TRADE_ITEMS = (
    {
        "sku": "RUTF-NUTRISET-92-150",
        "name": "Plumpy'Nut, 92 g sachet",
        "commodity_slug": "rutf",
        "manufacturer": "Nutriset",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 150,
        "base_unit_grams": 92,
        "shelf_life_months": 24,
        "spec_attributes": {"shelf_life_months": 24},
    },
    {
        "sku": "RUSF-NUTRISET-92-150",
        "name": "Plumpy'Sup, 92 g sachet",
        "commodity_slug": "rusf",
        "manufacturer": "Nutriset",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 150,
        "base_unit_grams": 92,
        "shelf_life_months": 24,
        "spec_attributes": {"shelf_life_months": 24},
    },
    {
        "sku": "F100-NUTRISET-114-90",
        "name": "F-100 therapeutic milk, 114 g sachet",
        "commodity_slug": "f100",
        "manufacturer": "Nutriset",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": 90,
        "base_unit_grams": 114,
    },
)
