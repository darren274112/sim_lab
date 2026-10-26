#!/bin/bash
echo "=== 军事仿真微服务算法验证沙箱演示 ==="
echo ""

echo "1. 查看帮助"
python wf.py --help 2>&1 | head -15
echo ""

echo "2. 列出可用配置"
ls -lh configs/
echo ""

echo "3. 演示单步调试算法"
echo "   命令: python wf.py run --config configs/military_fault_detect.yaml --id demo-001 --step detect"
echo ""

echo "4. 算法目录结构"
ls -lh algos/
echo ""

echo "5. 微服务清单（11个）"
grep -n "^metadata:" manifests/all-services.yaml | wc -l
echo "   共11个军事仿真微服务"
