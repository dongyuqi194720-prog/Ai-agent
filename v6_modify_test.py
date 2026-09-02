```python
def calculate_total(price, quantity, discount):
    return price * quantity * discount

def test_calculate_total():
    # 测试 case 1: 无 discount 参数
    result1 = calculate_total(10, 2, 0)
    assert result1 == 20, f"Expected 20, got {result1}"

    # 测试 case 2: 有 discount 参数
    result2 = calculate_total(10, 2, 0.5)
    assert result2 == 10, f"Expected 10, got {result2}"

    print("All tests passed!")

test_calculate_total()
```