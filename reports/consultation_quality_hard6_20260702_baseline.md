# consultation_quality_hard6_20260702_baseline

- consultation quality score: `75.267/100`
- pass rate: `0.0`
- passed cases: `0`
- failed cases: `6`
- transport error cases: `0`
- classification counts: `{'answer-level issue': 5, 'retrieval/package issue': 1}`

## Axis Averages

- `answer_material_score`: `0.799`
- `caveat_risk_score`: `1.0`
- `companion_regulation_presence_score`: `0.833`
- `confidence_score`: `1.0`
- `governing_system_presence_score`: `0.917`
- `issue_answer_score`: `0.671`
- `issue_context_score`: `0.882`
- `material_context_score`: `0.87`
- `practical_application_score`: `0.38`
- `precise_articles_answer_score`: `0.847`
- `precise_articles_context_score`: `0.847`
- `structure_score`: `1.0`

## Worst Cases

- `hard_005` score=`56.6` class=`retrieval/package issue` gaps=`['companion_regulation_presence_score', 'practical_application_score', 'answer_material_score', 'material_context_score', 'issue_answer_score']` missing_context_regs=`['nzam-mkafhh-jrymh-althrsh', 'workplace-behavioral-misconduct-controls']` missing_context_articles=`[6]` missing_answer_articles=`[6]`
- `hard_001` score=`73.9` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score', 'precise_articles_context_score', 'precise_articles_answer_score']` missing_context_regs=`[]` missing_context_articles=`[71, 74]` missing_answer_articles=`[71, 74]`
- `hard_003` score=`77.2` class=`answer-level issue` gaps=`['practical_application_score', 'precise_articles_context_score', 'precise_articles_answer_score', 'answer_material_score', 'issue_answer_score']` missing_context_regs=`[]` missing_context_articles=`[21]` missing_answer_articles=`[21]`
- `hard_006` score=`78.8` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score']` missing_context_regs=`[]` missing_context_articles=`[]` missing_answer_articles=`[]`
- `hard_002` score=`82.2` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score']` missing_context_regs=`[]` missing_context_articles=`[]` missing_answer_articles=`[]`
- `hard_004` score=`82.9` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score']` missing_context_regs=`[]` missing_context_articles=`[]` missing_answer_articles=`[]`

## Case Details

- `hard_001` score=`73.9` pass=`False` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score', 'precise_articles_context_score', 'precise_articles_answer_score']`
- `hard_002` score=`82.2` pass=`False` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score']`
- `hard_003` score=`77.2` pass=`False` class=`answer-level issue` gaps=`['practical_application_score', 'precise_articles_context_score', 'precise_articles_answer_score', 'answer_material_score', 'issue_answer_score']`
- `hard_004` score=`82.9` pass=`False` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score']`
- `hard_005` score=`56.6` pass=`False` class=`retrieval/package issue` gaps=`['companion_regulation_presence_score', 'practical_application_score', 'answer_material_score', 'material_context_score', 'issue_answer_score']`
- `hard_006` score=`78.8` pass=`False` class=`answer-level issue` gaps=`['practical_application_score', 'issue_answer_score']`
