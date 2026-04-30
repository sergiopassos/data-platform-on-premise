from agents.state import initial_state


def test_initial_state_fields():
    s = initial_state("customers")
    assert s["table_name"] == "customers"
    assert s["current_status"] == "RUNNING"
    assert s["kafka_topic"] == "cdc.public.customers"
    assert s["next_agent"] == "orchestrator"
    assert s["last_completed"] == ""
    assert s["error_log"] is None
    assert s["report_markdown"] is None
    assert s["agent_timings"] == {}
    assert s["scores"] == {}


def test_initial_state_run_id_unique():
    s1 = initial_state("t1")
    s2 = initial_state("t2")
    assert s1["run_id"] != s2["run_id"]
    assert s1["run_id"].startswith("e2e-")


def test_initial_state_custom_gold_table():
    s = initial_state("orders", gold_table_fqn="iceberg.gold.orders_summary")
    assert s["gold_table_fqn"] == "iceberg.gold.orders_summary"


def test_initial_state_default_gold_table():
    s = initial_state("customers")
    assert s["gold_table_fqn"] == "iceberg.gold.customers_orders"


def test_state_is_typeddict():
    s = initial_state("test")
    assert isinstance(s, dict)
    required_keys = {
        "run_id", "langfuse_trace_id", "current_status", "table_name",
        "gold_table_fqn", "data_contract_path", "kafka_topic", "error_log",
        "last_completed", "next_agent", "agent_timings", "scores", "report_markdown",
    }
    assert required_keys.issubset(s.keys())
