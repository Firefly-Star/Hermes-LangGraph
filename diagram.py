"""生成 LangGraph 工作流流程图。"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from workflow.graph import build_graph
from workflow.utils import setup_runtime


def main():
    output = os.path.join(os.path.dirname(__file__), "workflow_diagram.png")

    print("构建工作流图...")
    runtime = setup_runtime()
    app = build_graph(runtime)

    try:
        png = app.get_graph().draw_mermaid_png()
        with open(output, "wb") as f:
            f.write(png)
        print(f"  → PNG: {output}")
    except Exception as e:
        print(f"  PNG 生成失败（{e}），回退到 Mermaid 文本...")
        try:
            mermaid = app.get_graph().draw_mermaid()
            md_path = output.replace(".png", ".md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write("```mermaid\n" + mermaid + "\n```")
            print(f"  → Mermaid: {md_path}")
            print("  （可用 VS Code 预览 Mermaid 文件）")
        except Exception as e2:
            print(f"  Mermaid 生成也失败: {e2}")


if __name__ == "__main__":
    main()
