"""run.json の共通メタデータ（schema_version / qa_status）。

run.json は engine と algorithm の複数箇所から書かれるため、
付与処理をここへ集約する。既存キーは変更せず、追加のみ行う。
"""
RUN_JSON_SCHEMA_VERSION = 1
# 納品の合否は自動判定しない。常に未評価として記録する（status とは独立）。
QA_NOT_EVALUATED = 'not_evaluated'


def finalize_run_record(record):
    """schema_version と qa_status を付与して返す（record を直接更新）。

    qa_status は常に not_evaluated に固定する。過去の値が混入しても
    自動判定結果として扱わない。
    """
    record['schema_version'] = RUN_JSON_SCHEMA_VERSION
    record['qa_status'] = QA_NOT_EVALUATED
    return record
