evalPrompt = '''
Your task is to optimize the provided sequence calculation code, focusing on reducing time complexity to achieve extreme performance.
Requirements:
1. Code must be enclosed in a block.
2. The function signature must strictly adhere to `def calculator(n: int) -> int:`.
3. The code block must strictly exclude testing code, print statements, or `if __name__ == "__main__"`.

Code:
```python
{CODE}
```
'''.strip()