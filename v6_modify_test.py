def calculate_total(price, quantity, discount):
    """
    计算总价，包括价格、数量和折扣。

    参数:
    price (float): 单价
    quantity (int): 数量
    discount (float): 折扣

    返回:
    float: 总价
    """
    total = price * quantity * discount
    return total


def calculate_with_tax(price, quantity, discount, tax):
    total = calculate_total(price, quantity, discount)
    return total + tax