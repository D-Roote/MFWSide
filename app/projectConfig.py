"""Validation for the single selected PI resource; overlay paths stay ordered."""
def selected_resource(interface):
    resources = interface.get("resource", [])
    if not isinstance(resources, list) or not resources:
        raise ValueError("interface.json의 resource는 비어 있지 않은 객체 목록이어야 합니다.")
    resource = resources[0]
    if not isinstance(resource, dict):
        raise ValueError("interface.json의 첫 번째 resource 항목은 객체여야 합니다.")
    paths = resource.get("path")
    if not isinstance(paths, list) or not paths:
        raise ValueError("설정된 리소스 경로 목록이 비어 있거나 목록 형식이 아닙니다.")
    if not all(isinstance(path, str) and path.strip() and "\0" not in path for path in paths):
        raise ValueError("모든 리소스 경로는 비어 있지 않은 유효한 문자열이어야 합니다.")
    return resource
