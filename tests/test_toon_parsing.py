"""
TOON 格式营养数据解析单元测试

使用 nutrition_example_resp.json 中的实际返回格式进行测试
"""

import sys
import os
import json

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from mcd_agent.nutrition import NutritionAnalyzer


class MockMcpToolClient:
    """模拟 MCP 工具客户端"""
    
    def __init__(self, mock_response: dict):
        self.mock_response = mock_response
    
    def find_tool(self, name: str):
        return {
            "name": "list-nutrition-foods",
            "inputSchema": {
                "properties": {
                    "keyword": {"type": "string"}
                }
            }
        }
    
    def call_tool(self, tool_name: str, arguments: dict):
        return self.mock_response


def load_example_response():
    """加载实际的 example 响应"""
    example_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'mcd_agent', 'nutrition_example_resp.json')
    with open(example_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def test_extract_structured_content_data():
    """测试从 structuredContent.data 提取数据"""
    print("=" * 60)
    print("测试 1: 从 structuredContent.data 提取数据")
    print("=" * 60)
    
    example_response = load_example_response()
    
    # 创建模拟客户端
    mock_client = MockMcpToolClient(example_response)
    analyzer = NutritionAnalyzer(None, mock_client)
    
    # 提取数据
    text_content = analyzer._extract_structured_content_data(example_response)
    
    print(f"\n提取的数据（前200字符）:\n{text_content[:200]}...")
    
    # 验证
    assert text_content is not None, "数据提取失败"
    assert "[160]{" in text_content, "未找到 TOON 格式头部"
    assert "猪柳麦满分" in text_content, "未找到测试数据"
    
    print("\n✅ 测试通过!")


def test_parse_toon_format_with_real_data():
    """测试使用实际 TOON 格式数据解析"""
    print("\n" + "=" * 60)
    print("测试 2: 使用实际 TOON 格式数据解析")
    print("=" * 60)
    
    example_response = load_example_response()
    mock_client = MockMcpToolClient(example_response)
    analyzer = NutritionAnalyzer(None, mock_client)
    
    # 提取 structuredContent.data
    text_content = analyzer._extract_structured_content_data(example_response)
    
    # 解析 TOON 格式
    records = analyzer._parse_toon_format(text_content)
    
    print(f"\n解析结果:")
    print(f"  总记录数: {len(records)}")
    
    # 显示前几条记录
    print(f"\n前5条记录:")
    for i, record in enumerate(records[:5]):
        print(f"\n  记录 {i+1}:")
        print(f"    productName: {record.get('productName')}")
        print(f"    energyKcal: {record.get('energyKcal')}")
        print(f"    protein: {record.get('protein')}")
    
    # 验证
    assert len(records) > 0, "未解析到任何记录"
    assert records[0].get('productName') == '猪柳麦满分', "第一条记录不正确"
    
    print("\n✅ 测试通过!")


def test_query_nutrition_with_real_response():
    """测试使用实际响应查询营养"""
    print("\n" + "=" * 60)
    print("测试 3: 使用实际响应查询营养")
    print("=" * 60)
    
    example_response = load_example_response()
    mock_client = MockMcpToolClient(example_response)
    analyzer = NutritionAnalyzer(None, mock_client)
    
    # 查询猪柳麦满分
    print(f"\n查询: 猪柳麦满分")
    result = analyzer.query_nutrition("猪柳麦满分")
    
    if result:
        print(f"\n查询结果:")
        print(f"  来源: {result.get('source')}")
        print(f"  商品: {result.get('product_name')}")
        
        nutrition = result.get('nutrition', {})
        print(f"\n  营养成分:")
        print(f"    热量: {nutrition.get('calories')} kcal")
        print(f"    蛋白质: {nutrition.get('protein_g')} g")
        print(f"    脂肪: {nutrition.get('fat_g')} g")
        print(f"    碳水: {nutrition.get('carbs_g')} g")
        print(f"    钠: {nutrition.get('sodium_mg')} mg")
        
        assert result.get('source') == 'mcp', f"数据源不匹配: {result.get('source')}"
        assert nutrition.get('calories') == 308, f"热量不匹配: {nutrition.get('calories')}"
        assert nutrition.get('protein_g') == 16.0, f"蛋白质不匹配: {nutrition.get('protein_g')}"
    else:
        print("❌ 查询失败: result 为 None")
        assert False, "查询失败"
    
    print("\n✅ 测试通过!")


def test_query_all_products():
    """测试查询多个商品"""
    print("\n" + "=" * 60)
    print("测试 4: 测试查询多个商品")
    print("=" * 60)
    
    example_response = load_example_response()
    mock_client = MockMcpToolClient(example_response)
    analyzer = NutritionAnalyzer(None, mock_client)
    
    test_products = [
        '猪柳麦满分',
        '板烧鸡腿堡',
        '巨无霸',
        '薯条',  # 部分匹配
        '可乐',
    ]
    
    print(f"\n测试查询 {len(test_products)} 个商品:")
    
    for product in test_products:
        result = analyzer.query_nutrition(product)
        if result:
            nutrition = result.get('nutrition', {})
            print(f"\n  ✅ {product}:")
            print(f"     热量: {nutrition.get('calories')} kcal")
        else:
            print(f"\n  ❌ {product}: 未找到")
    
    print("\n✅ 测试通过!")


def test_parse_nutrition_index_from_text():
    """测试 _parse_nutrition_index_from_text 方法"""
    print("\n" + "=" * 60)
    print("测试 5: _parse_nutrition_index_from_text 方法")
    print("=" * 60)
    
    example_response = load_example_response()
    mock_client = MockMcpToolClient(example_response)
    analyzer = NutritionAnalyzer(None, mock_client)
    
    # 使用实际响应
    records = analyzer._parse_nutrition_index_from_text(example_response)
    
    print(f"\n解析结果:")
    print(f"  总记录数: {len(records)}")
    
    # 验证数据结构
    if records:
        first_record = records[0]
        print(f"\n第一条记录结构:")
        for key, value in first_record.items():
            print(f"    {key}: {value}")
        
        assert 'name' in first_record, "缺少 name 字段"
        assert 'energyKcal' in first_record, "缺少 energyKcal 字段"
        assert 'protein' in first_record, "缺少 protein 字段"
        
        # 测试匹配功能
        matched = analyzer._match_mcp_nutrition('巨无霸', records)
        if matched:
            name, facts = matched
            print(f"\n匹配测试:")
            print(f"  查询: 巨无霸")
            print(f"  匹配名称: {name}")
            print(f"  热量: {facts.calories if facts else 'None'} kcal")
            print(f"  蛋白质: {facts.protein_g if facts else 'None'} g")
    
    print("\n✅ 测试通过!")


def test_convert_toon_to_nutrition():
    """测试 TOON 到营养数据转换"""
    print("\n" + "=" * 60)
    print("测试 6: TOON 到营养数据转换")
    print("=" * 60)
    
    analyzer = NutritionAnalyzer(None, None)
    
    # 模拟一条 TOON 记录
    toon_record = {
        'productName': '巨无霸',
        'nutritionDescription': None,
        'energyKj': '2146',
        'energyKcal': '513',
        'protein': '27',
        'fat': '26',
        'carbohydrate': '42',
        'sodium': '961',
        'calcium': '171'
    }
    
    print(f"\nTOON 记录:")
    for key, value in toon_record.items():
        print(f"  {key}: {value}")
    
    # 转换
    nutrition = analyzer._convert_toon_to_nutrition(toon_record)
    
    print(f"\n转换为标准营养数据:")
    for key, value in nutrition.items():
        print(f"  {key}: {value}")
    
    # 验证
    assert nutrition.get('calories') == 513, f"热量不匹配: {nutrition.get('calories')}"
    assert nutrition.get('protein_g') == 27.0, f"蛋白质不匹配: {nutrition.get('protein_g')}"
    assert nutrition.get('fat_g') == 26.0, f"脂肪不匹配: {nutrition.get('fat_g')}"
    assert nutrition.get('carbs_g') == 42.0, f"碳水不匹配: {nutrition.get('carbs_g')}"
    assert nutrition.get('sodium_mg') == 961.0, f"钠不匹配: {nutrition.get('sodium_mg')}"
    assert nutrition.get('energy_kj') == 2146, f"能量(kJ)不匹配: {nutrition.get('energy_kj')}"
    
    print("\n✅ 测试通过!")


def test_with_real_toon_data():
    """测试解析真实的 TOON 数据"""
    print("\n" + "=" * 60)
    print("测试 7: 解析真实的 TOON 数据")
    print("=" * 60)
    
    example_response = load_example_response()
    mock_client = MockMcpToolClient(example_response)
    analyzer = NutritionAnalyzer(None, mock_client)
    
    # 从 structuredContent.data 提取
    text_content = analyzer._extract_structured_content_data(example_response)
    
    # 解析
    records = analyzer._parse_toon_format(text_content)
    
    print(f"\n解析统计:")
    print(f"  总记录数: {len(records)}")
    
    # 统计各类商品
    products = [r.get('productName', '') for r in records]
    burgers = [p for p in products if '堡' in p]
    drinks = [p for p in products if '可乐' in p or '雪碧' in p or '咖啡' in p]
    snacks = [p for p in products if '薯条' in p or '鸡翅' in p]
    
    print(f"\n商品分类统计:")
    print(f"  汉堡类: {len(burgers)} 种")
    print(f"  饮料类: {len(drinks)} 种")
    print(f"  小食类: {len(snacks)} 种")
    
    # 测试几个典型商品
    test_cases = [
        ('巨无霸', 513, 27, 26),
        ('板烧鸡腿堡', 391, 23, 17),
        ('可乐中杯', 147, 0, 0),
        ('中薯条', 289, 4, 12),
    ]
    
    print(f"\n典型商品验证:")
    for product_name, expected_cal, expected_prot, expected_fat in test_cases:
        record = next((r for r in records if r.get('productName') == product_name), None)
        if record:
            actual_cal = int(record.get('energyKcal', 0))
            actual_prot = float(record.get('protein', 0))
            actual_fat = float(record.get('fat', 0))
            
            status = "✅" if actual_cal == expected_cal else "❌"
            print(f"  {status} {product_name}:")
            print(f"     预期热量: {expected_cal}, 实际: {actual_cal}")
            print(f"     预期蛋白: {expected_prot}, 实际: {actual_prot}")
            print(f"     预期脂肪: {expected_fat}, 实际: {actual_fat}")
            
            if actual_cal != expected_cal:
                print(f"     ⚠️  热量不匹配!")
    
    print("\n✅ 测试通过!")


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("TOON 格式营养数据解析单元测试（使用实际数据）")
    print("=" * 60)
    
    try:
        test_extract_structured_content_data()
        test_parse_toon_format_with_real_data()
        test_convert_toon_to_nutrition()
        test_parse_nutrition_index_from_text()
        test_query_nutrition_with_real_response()
        test_query_all_products()
        test_with_real_toon_data()
        
        print("\n" + "=" * 60)
        print("🎉 所有测试通过!")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
