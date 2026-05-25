"""
Write Claude-generated labels for data/eval/er_labels.jsonl.

Each label was decided by reading the YC company's full description against
the full Crunchbase candidate set + (for absent rows) fuzzy near-matches,
using content signals Tier 1 doesn't see (one_liner, founders, industries,
locations). All decisions documented in the `notes` field.

Labeled_by = "claude_auto"; flagged for human review.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "data" / "eval" / "er_labels_TEMPLATE.jsonl"
OUT_PATH = REPO_ROOT / "data" / "eval" / "er_labels.jsonl"

# Map yc_record_id -> (ground_truth_cb_id, notes). cb_id=None means absent.
LABELS = {
    # ---------- easy / matched_unique (clean, high-confidence matches) ----------
    "yc:clinikally":          ("cb:3722ba39-3fef-416f-8c28-f7c948abadc6", "domain+name+content match; both dermatology telehealth India"),
    "yc:conta-simples":       ("cb:2a80720f-af37-4d07-8a38-91eec7acb799", "domain+name match; both Latam SMB fintech, same product"),
    "yc:credibook":           ("cb:ae05254b-9e90-441b-8d6f-11d461bd83d3", "domain+name match; both Indonesia MSME wholesale bookkeeping"),
    "yc:cyble":               ("cb:e92c942f-e4f9-443f-bb1d-1ef2e93c50f8", "domain+name match; both cybersecurity threat intel"),
    "yc:directshifts":        ("cb:692d6600-3c40-464f-995d-5814411f6ad0", "domain+name match; healthcare staffing marketplace"),
    "yc:embrace":             ("cb:6d035536-8d2c-e457-99cc-3efca72cb580", "domain match (embrace.io); both mobile observability"),
    "yc:fondeadora":          ("cb:752e4acb-a4ce-b2dc-28d5-a5d791f380e8", "domain+name match; both Mexico digital banking"),
    "yc:karbon-card":         ("cb:5c5e71ce-3df2-41a7-9baf-cf0c94b492e4", "domain match; CB name shorter (Karbon vs Karbon Card) but same product/domain"),
    "yc:kredi":               ("cb:4e88c072-6d59-45ab-98de-d155a402f84e", "domain match (kredi.mx); both Latam mortgage"),
    "yc:markaz-technologies": ("cb:4553f6be-774f-44fb-b995-bbbc525e6c90", "domain+name match; Pakistan reselling marketplace"),
    "yc:medme-health":        ("cb:7c406a05-d965-416e-861d-b315d4ea80a7", "domain+name match; pharmacy clinical services SaaS"),
    "yc:roboflow":            ("cb:5247dfd7-ae62-4495-a6e9-f5e9c8423140", "domain+name match; CV developer tools"),
    "yc:todos-comemos":       ("cb:b896e01e-7ead-4010-9923-997204b6e0ba", "domain+name match; Colombia pre-made meals"),
    "yc:yassir":              ("cb:b01a9c91-e915-4737-86b6-e34adf12283d", "domain match; both Algeria super-app"),

    # ---------- random / matched_unique ----------
    "yc:bobidi":              ("cb:22b94477-083d-44ae-9e1f-110d69d9aa1e", "Pivot case: YC current is Upswell (restaurant loyalty), formerly Bobidi (recruitment AI in CB). Same name + year + W22; YC.former_names confirms identity"),
    "yc:brainboard":          ("cb:840ccd53-be29-44f7-9fab-d56903ac9a4b", "domain+name match; visual cloud infra design"),
    "yc:chaser":              (None, "Tier 1 false positive. Same name+year only. YC.domain=null (no corroboration); CB domain=usechaser.com differs; descriptions diverge (no-code app builder vs CS workflow); CB status=closed; 'Chaser' is generic. Insufficient evidence for match"),
    "yc:endla":               ("cb:1e21f98c-3971-46a1-8d81-47a5c445ab41", "domain+name match; oil & gas well software"),
    "yc:gamebytes":           ("cb:053a0226-0a6e-4d1f-8734-951d4d33e78b", "domain+name match; social gaming app"),
    "yc:genomelink":          ("cb:82ac3216-92ea-4079-8300-0d876e04d13a", "domain match (genomelink.io); DNA app platform"),
    "yc:koshex":              ("cb:80cf2191-d86b-40d2-a4b4-2867ae628ac2", "domain+name match; identical one_liner about Indian wealth automation"),
    "yc:lucid-bots":          ("cb:34f63865-fcee-44a9-8d5e-d8e8f1530521", "domain+name match; cleaning drone robotics"),
    "yc:modernbanc":          ("cb:2a4bb0e2-b230-4542-9bd2-705b7069277e", "domain+name match; financial ops platform"),
    "yc:nirva":               ("cb:5e29cac8-1523-4107-afa9-a2913b30d935", "domain match (nirvahealth.com); healthcare lifestyle medicine"),
    "yc:promptloop":          (None, "Tier 1 false positive via former_name. YC.former_names=['Kiter']; CB Kiter is a job search app (Peter Mangan founder) on kiter.app. PromptLoop is AI agents for B2B datasets on promptloop.com. Different domains, different products, no founder corroboration. 'Kiter' is a generic name shared by multiple companies"),
    "yc:resquared":           ("cb:29dba283-a5c6-4465-aedc-f8bfb99ef5c9", "domain+name match (re2.ai); marketing automation for local biz"),
    "yc:sei":                 ("cb:632a66b6-e3db-41da-aa87-3f5f36bdf488", "domain+name match (seiright.com); AI compliance for financial services"),
    "yc:shinkei-systems":     ("cb:b9939912-bef7-4c1c-ae5c-490772c0d8f6", "domain+name match (shinkei.systems); fish harvesting automation"),
    "yc:solum-health":        ("cb:43916811-4264-47be-8586-befca51c1d88", "Rebrand match via former_name='Momentu'. Verified pivot: co-founder Juan Pablo Montoya moved the company from Bogota to SF and pivoted from Latam B2C mental health coaching (Momentu) to US-based AI operating system for healthcare practices (Solum Health). Same corporate entity, dramatic pivot — exactly the case Tier 1's former_names lookup is designed to catch"),
    "yc:volta-labs-inc":      ("cb:bf5f7dd1-018a-4df4-8fd6-95fd49aea9c8", "domain+name match (voltalabs.com); genomics digital fluidics"),
    "yc:volumetric":          ("cb:06b8a7c3-3198-4afe-91c9-19009863044b", "domain+name match (volumetricbio.com); biofabrication tissue engineering"),
    "yc:warpfy":              ("cb:5e155349-d400-4838-8c7f-f273eb9b3ef3", "domain+name match; e-commerce home brand"),

    # ---------- rebrand / matched_unique ----------
    "yc:dojah-inc":           ("cb:e186fa2f-a617-4475-8277-10377f992c0f", "domain+name match (dojah.io); KYC/fraud prevention Nigeria"),
    "yc:frubana-inc":         ("cb:9f2a2eb8-3e42-88ff-4d7c-6ae5e6794eed", "domain+name match (frubana.com); Latam restaurant supply"),
    "yc:lemfi":               ("cb:2b67d957-4f54-4d8d-8202-2bfa4ba681bd", "domain+name match (lemfi.com); cross-border banking for migrants"),
    "yc:micro-meat":          ("cb:8327404a-91ac-40fb-a1e2-1b243fb6215e", "domain+name match (micromeat.com); cultivated meat scaling"),
    "yc:penciled":            ("cb:adeb8270-1ac3-4b04-8b4b-7f04518e442b", "Rebrand match via former_name='Somn'. YC Penciled (PT front office) ← CB Somn (healthcare AI receptionists). Same domain root pattern, same product"),
    "yc:radmate-ai":          ("cb:1716fc9e-8875-474f-bd3a-0845fcc41e06", "domain+name match (radmate.ai); radiology copilot"),
    "yc:rownd":               (None, "Tier 1 false positive on name+year. YC Rownd (rownd.ai, AI deployment platform, formerly LlamaFarm) vs CB Rownd (rownd.com, B2B SaaS auth). Different domains, completely different products. Two different companies sharing the Rownd name"),
    "yc:sevnai":              ("cb:68e9bf15-0508-4a9e-b9f2-eac293299829", "domain+name+year exact match (sevn.ai). YC's enormous former_names list (Sevn.ai, AdRizz, PySpur, Rehearsal.so, Ramble.gg, Sagaland) confirms a pivot history; CB caught at one such pivot point"),
    "yc:spinach-ai":          ("cb:57998bb5-bbad-4c1a-81ba-082bed2f1b82", "Rebrand via former_name='Spinach.io'. spinach.io→spinach.ai, same meeting AI product"),
    "yc:ybanq":               ("cb:14b366f6-dbd3-4d51-b953-89ad563dc636", "domain+name match (ybanq.com); India B2B reconciliation"),

    # ---------- weak_domain / matched_unique ----------
    "yc:commodityai":         ("cb:6025a2ff-56da-4aa2-b9b3-d7d6c5024210", "domain+name match (commodityai.io); identical product description"),
    "yc:coperniq":            ("cb:baea2b4a-f49f-44d9-a4e9-41748c8b4798", "domain match (coperniq.io); CB name has 'Inc.' suffix; identical one_liner"),
    "yc:lotus":               ("cb:b75a3aed-ceee-4876-b075-22bec7cbed0e", "domain+name match (uselotus.io); open-source pricing engine"),
    "yc:mayan":               ("cb:25b6493d-7a27-49d8-a829-395e627e9c24", "domain+name match (mayan.co); Amazon seller automation, identical one_liner"),
    "yc:menten-ai":           ("cb:d0fd10c0-1f16-4570-a6e9-8c035b7a933f", "domain match (menten.ai); protein design quantum ML"),
    "yc:noya":                ("cb:3fb37710-79fe-41dc-a146-550751216cc6", "domain+name match (noya.co); direct air capture retrofit"),
    "yc:trainy":              ("cb:ff7127a8-1376-475a-8cd7-65c4e77d36b3", "domain+name match (trainy.ai); GPU cluster orchestration"),
    "yc:unsloth-ai":          ("cb:1b182e3a-6abc-4c14-b081-61887384422e", "domain+name match (unsloth.ai); LLM fine-tuning/RL"),
    "yc:waza":                ("cb:89204b34-7e10-4d62-ac83-b9edaf6e6f71", "domain+name match (waza.co); Nigeria B2B payments"),

    # ---------- tier1_ambiguous (5+ candidates; pick the right one) ----------
    "yc:fathom":              ("cb:b2f61c45-6a26-4655-bfd6-787c3b4b3899", "Of 4 Fathom candidates, c2 (fathom.video) is the right one: AI Zoom notetaker. Others are unrelated (podcasts, life sciences, privacy/relationships)"),
    "yc:hadrius":             ("cb:2888e1e8-05e5-42b3-92ad-be3d6d641fc2", "Of 2 candidates: c1 (Hadrius hadrius.com) is current YC entity. c0 (Quantbase) is same team pre-pivot — founder triplet matches exactly across both"),
    "yc:mesh":                ("cb:1d38a30a-3c5f-49a3-866c-4298d1471f24", "Of 5 Mesh candidates, c0 (mesh.ai) is exact domain match + identical product (social performance management)"),
    "yc:onekey":              (None, "Domain-recycling false positive. YC OneKey (EV charging site selection, founder team in France) and CB OneKey at getonekey.io (mobile keyboard CRM, founder Christophe Barre) are independent entities that happened to use the same domain at different times. Different founding teams, different geographies, no corporate lineage. Tier 1's domain rule cannot distinguish domain reuse after a company dies — exactly the case Tier 2's founder/description/location signals would catch"),
    "yc:revamp":              ("cb:313d8f10-c1ad-42af-922e-1d470bad6366", "Of 2 candidates, c0 (Revamp AI getrevamp.ai) is exact domain match. c1 RevAmp on rev-amp.ai is a different company"),
    "yc:superpowered-ai":     ("cb:f6fa9dee-746f-4961-bc52-07502ed212ad", "Of 2 candidates, c1 (Superpowered AI) is current. c0 (Levo Financial) is same team pre-pivot — 4-founder set matches exactly across both"),
    "yc:verto":               ("cb:33174dd1-83fc-40f5-8710-b92fc01bd856", "Of 4 Verto candidates, c0 (vertofx.com) is exact domain match; cross-border payments for emerging markets"),
    "yc:buildbuddy":          ("cb:11b947f3-37c1-450a-9c49-2e7d8b0feda9", "Of 2 BuildBuddy candidates, c0 (buildbuddy.io) is exact domain + Bazel product match; c1 is UK construction"),
    "yc:dashblock":           ("cb:203e52dc-7fde-48b8-8f58-ce9003e48a83", "Of 2 candidates, c0 (Dashblock) is current. c1 (Datap) is same team pre-rename — founder pair matches"),
    "yc:flint":               ("cb:d8b9c891-69ba-430b-b1ec-654abf5003b9", "Of 4 Flint candidates, c3 (flintnurse.com) is the healthcare-staffing match for YC's nurse-recruiting product. YC.domain (withflint.com) differs but the specific product description is identical. c1 (Oco Meals) is same entity pre-pivot via former_names"),
    "yc:henry":               ("cb:df0c5c20-b76d-4640-976e-3e708ed518a0", "Of 2 candidates, c1 (HENRY soyhenry.com) is exact domain match for software developer training in Argentina"),
    "yc:juicy-marbles":       ("cb:34b096b5-9d2d-4bdf-b4b2-7c9580388886", "Of 2 candidates, c0 (juicymarbles.com) is exact domain + plant-based steaks product match"),
    "yc:spring-in-africa":    ("cb:d26d9d8a-16e4-4a0b-b6cf-bc996e94f9e2", "Of 5 candidates, c3 (Wallets Africa, wallets.africa) is the match via former_names. CB one_liner 'Investing in innovation' is identical to YC's one_liner"),
    "yc:aviaryai":            ("cb:76c49cbf-dcf7-4394-a8e1-1f864612780a", "Of 4 candidates, c1 (AviaryAI helloaviary.ai) is exact domain match for current YC entity. c3 (Cambio cambiomoney.com, credit rebuilding) is same team pre-pivot — founder 'Blesson Abraham' appears in both"),
    "yc:kopa":                ("cb:ccf94d51-195d-f275-785a-39bcf22fb33c", "Of 2 candidates, c1 (kopa.co) is exact domain + furnished rentals product match"),
    "yc:magicflow":           ("cb:91df7c94-be2f-4e99-8fa8-f23e3641f340", "Of 2 candidates, c0 (Magicflow magicflow.ai) is exact domain match. Israeli founders + Israeli YC location confirm"),
    "yc:matter":              (None, "Tier 1 false positive cluster. YC Matter is a reading app on getmatter.app. None of the 5 CB Matter candidates (music marketplace, energy storage, recycling, etc.) are reading apps. YC entity not in CB"),

    # ---------- absent_from_crunchbase ----------
    "yc:astro-mechanica":     (None, "True absent. Near matches (Mechanica branding agency, Mechanica manufacturing) are unrelated"),
    "yc:flux-auto":           ("cb:b72d94ca-02fc-2aef-6308-75a1ababd687", "Tier 1 false negative. Near match nm1 (Flux Auto, fluxauto.xyz, autonomous trucks) is the same company on a different TLD. Tier 1 missed because CB founded_year=2017 vs YC=2022 (delta=5, outside ±2 window) — strict null-year design intentionally"),
    "yc:infisical":           (None, "True absent. No CB record for the secrets management product on infisical.com"),
    "yc:inito":               (None, "True absent. Fuzzy matches (ITO, Adfinito, Maginito) all unrelated to hormone tracking"),
    "yc:increase":            (None, "True absent. No CB row for the banking-APIs increase.com product"),
    "yc:wedge":               (None, "True absent. Wedge healthcare AI not in CB; the Wedge HR and Wedge corona-discharge rows are unrelated"),
    "yc:cedar":               (None, "True absent. None of the Cedar fuzzy matches are AI sales playbook tooling"),
    "yc:rocketable":          (None, "True absent. Rocketable AI holding company not in CB"),
    "yc:superagent":          (None, "True absent. No fuzzy candidates found; AI red-teaming product not in CB"),
    "yc:verne-robotics":      (None, "True absent. Verne Robotics arm-control AI not in CB"),
    "yc:hyperbound":          (None, "True absent. No fuzzy candidates; sales activation platform not in CB"),
    "yc:perspectives-health": (None, "True absent. Perspective fuzzy matches (VR diversity training, marketing agencies) unrelated"),
}


def main():
    with TEMPLATE_PATH.open() as f:
        rows = [json.loads(line) for line in f]

    template_ids = {r["yc_record_id"] for r in rows}
    label_ids = set(LABELS.keys())
    if template_ids != label_ids:
        missing = template_ids - label_ids
        extra = label_ids - template_ids
        if missing:
            print(f"WARNING: labels missing for {len(missing)} template rows:")
            for m in sorted(missing):
                print(f"  {m}")
        if extra:
            print(f"WARNING: labels for {len(extra)} ids not in template:")
            for e in sorted(extra):
                print(f"  {e}")
        if missing:
            raise SystemExit("incomplete labels")

    with OUT_PATH.open("w") as f:
        for r in rows:
            gold, notes = LABELS[r["yc_record_id"]]
            r["ground_truth_cb_id"] = gold
            r["notes"] = notes
            r["labeled_by"] = "claude_auto"
            f.write(json.dumps(r) + "\n")

    n_present = sum(1 for v in LABELS.values() if v[0] is not None)
    n_absent  = sum(1 for v in LABELS.values() if v[0] is None)
    print(f"Wrote {OUT_PATH}  ({len(rows)} rows)")
    print(f"  ground_truth present: {n_present}")
    print(f"  ground_truth absent:  {n_absent}")


if __name__ == "__main__":
    main()
