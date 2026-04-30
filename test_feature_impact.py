"""Test feature impact analysis."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.trellis.feature_impact import ProjectContextParser, FeatureImpactAnalyzer
from src.trellis import CodeGraphBridge


def test_parser():
    print("Testing ProjectContextParser...")
    print("-" * 50)
    
    project_path = str(Path(__file__).parent / "tests")
    parser = ProjectContextParser(project_path)
    
    features = parser.get_all_features()
    print(f"Found {len(features)} features")
    
    for name, feature in features.items():
        print(f"\n[FEAT] {name}")
        print(f"   Description: {feature.description[:60]}...")
        print(f"   Decisions: {len(feature.decisions)}")
        print(f"   Constraints: {len(feature.constraints)}")
        print(f"   File patterns: {feature.file_patterns}")
        print(f"   Dependencies: {feature.dependencies}")
        print(f"   Status: {feature.status}")
        
        for d in feature.decisions:
            print(f"   [DEC] {d.decision_id}: {d.description}")
            for c in d.constraints:
                print(f"      - {c}")
    
    # Test file mapping
    test_files = [
        "src/auth/login.py",
        "src/payments/stripe.py",
        "src/users/profile.py",
        "src/export/csv.py",
        "src/random/file.py",
    ]
    
    print("\n\nFile mapping test:")
    for f in test_files:
        feature = parser.get_feature_for_file(f)
        if feature:
            print(f"  {f} -> {feature.feature_name}")
        else:
            print(f"  {f} -> (no feature)")
    
    return features


def test_feature_impact(features):
    print("\n\nTesting FeatureImpactAnalyzer...")
    print("-" * 50)
    
    project_path = str(Path(__file__).parent.parent)
    bridge = CodeGraphBridge(project_path)
    analyzer = FeatureImpactAnalyzer(bridge, str(Path(__file__).parent / "tests"))
    
    # Test getting context for a function
    print("\nFeature context for 'analyze_impact':")
    # Use a function that exists in our code
    func_name = "analyze_impact"
    
    # Get development pointers
    pointers = analyzer.get_development_pointers(func_name)
    print(f"Pointers ({len(pointers)}):")
    for p in pointers:
        print(f"  - {p}")
    
    # Test divergence check
    print("\nDivergence check:")
    divergence = analyzer.check_divergence(func_name)
    for d in divergence:
        print(f"  - {d}")
    
    bridge.close()


def main():
    print("Feature Impact Analysis Tests")
    print("=" * 60)
    
    features = test_parser()
    test_feature_impact(features)
    
    print("\n" + "=" * 60)
    print("[PASS] Feature impact tests completed")


if __name__ == "__main__":
    main()
