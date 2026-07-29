from __future__ import annotations
from typing import Any, Mapping, Sequence
from .model import K_POLICY, floor_build_evaluation_ledger, floor_build_tournament_report, floor_seal_evaluation_policy

def floor_default_evaluation_policy()->dict[str,Any]:
    return floor_seal_evaluation_policy({'schema_version':1,'kind':K_POLICY,'policy_id':'floor_reference_quality_v1','require_independent_evaluator':True,'hard_gates':[{'gate_id':'custody','metric':'custody_passed','operator':'eq','value':1},{'gate_id':'complete','metric':'complete_execution','operator':'eq','value':1}],'objective_stages':[{'stage_id':'musical_usefulness','metrics':[{'metric':'musical_usefulness','weight':1,'direction':'max'},{'metric':'reference_legibility','weight':1,'direction':'max'}]},{'stage_id':'cost_and_reproducibility','metrics':[{'metric':'repeatability','weight':1,'direction':'max'},{'metric':'runtime_seconds','weight':.01,'direction':'min'}]}],'metadata':{'ranking':'hard gates then lexicographic stages','winner_scope':'benchmark_winner_only'}})
def floor_run_tournament(*,policy:Mapping[str,Any],candidates:Sequence[Mapping[str,Any]])->dict[str,Any]:
    evaluations=[floor_build_evaluation_ledger(policy=policy,provider_id=str(v['provider_id']),provider_version=str(v['provider_version']),result_semantic_sha256=str(v['result_semantic_sha256']),evaluator_id=str(v['evaluator_id']),metrics=dict(v.get('metrics') or {})) for v in candidates]
    return {'ok':True,'policy':floor_seal_evaluation_policy(policy),'evaluations':evaluations,'report':floor_build_tournament_report(policy=policy,evaluations=evaluations),'canonical_selection_applied':False}
__all__=['floor_default_evaluation_policy','floor_run_tournament']
