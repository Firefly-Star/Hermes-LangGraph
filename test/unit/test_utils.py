"""utils.py 纯函数测试：extract_plan_index / extract_current_step。"""
import pytest
from workflow.utils import extract_plan_index, extract_current_step


SAMPLE_PLAN = """## Step 1: 需求分析
内容1
## Step 2: 架构设计
内容2
## Step 3: 实现功能
内容3
## Step 4: 系统测试
内容4
"""


class TestExtractPlanIndex:
    """extract_plan_index — 从 plan 文本提取进度索引。"""

    def test_mark_completed_and_current(self):
        """completed=2: Step 1-2 标记 [x]，Step 3 标记 [>]，Step 4 标记 [ ]。"""
        result = extract_plan_index(SAMPLE_PLAN, 2)
        lines = result.split("\n")
        assert lines[0].startswith("[x]")
        assert lines[1].startswith("[x]")
        assert lines[2].startswith("[>]")
        assert lines[3].startswith("[ ]")

    def test_all_completed(self):
        """completed=4: 所有步骤标记 [x]，无 [>] 和 [ ]。"""
        result = extract_plan_index(SAMPLE_PLAN, 4)
        assert all(line.startswith("[x]") for line in result.split("\n"))

    def test_none_completed(self):
        """completed=0: Step 1 标记 [>]，其余 [ ]。"""
        result = extract_plan_index(SAMPLE_PLAN, 0)
        lines = result.split("\n")
        assert lines[0].startswith("[>]")
        assert all(line.startswith("[ ]") for line in lines[1:])

    def test_empty_text(self):
        """空文本返回空字符串。"""
        assert extract_plan_index("", 0) == ""

    def test_no_step_headers(self):
        """没有 Step 标题时返回空字符串。"""
        assert extract_plan_index("纯文本内容\n没有步骤标题\n", 0) == ""

    def test_keeps_title_content(self):
        """索引保留步骤标题内容。"""
        result = extract_plan_index(SAMPLE_PLAN, 0)
        assert "需求分析" in result.split("\n")[0]


class TestExtractCurrentStep:
    """extract_current_step — 从 plan 文本提取指定步骤全文。"""

    def test_returns_correct_step(self):
        """step_idx=1 返回 Step 2 的完整内容。"""
        result = extract_current_step(SAMPLE_PLAN, 1)
        assert result.startswith("## Step 2:")
        assert "内容2" in result

    def test_out_of_range_returns_empty(self):
        """超出步数范围返回空字符串。"""
        assert extract_current_step(SAMPLE_PLAN, 99) == ""

    def test_empty_text(self):
        """空文本返回空字符串。"""
        assert extract_current_step("", 0) == ""

    def test_first_step(self):
        """step_idx=0 返回第一个 Step。"""
        result = extract_current_step(SAMPLE_PLAN, 0)
        assert "## Step 1:" in result
        assert "内容1" in result
