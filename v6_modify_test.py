def calculate_total(price, quantity, discount=0):
    return price * quantity * discount

def test_calculate_total():
    # 测试 discount 参数为 0 的情况
    result = calculate_total(10, 2, 0)
    assert result == 20, "测试 discount 参数为 0 的情况失败"

    # 测试 discount 参数为 0.5 的情况
    result = calculate_total(10, 2, 0.5)
    assert result == 10, "测试 discount 参数为 0.5 的情况失败"

    # 测试 discount 参数为 0.1 的情况
    result = calculate_total(10, 2, 0.1)
    assert result == 2, "测试 discount 参数为 0.1 的情况失败"

    print("所有测试通过")