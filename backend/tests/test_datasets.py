def test_create_and_list_dataset(client, db_session) -> None:
    create_response = client.post("/datasets", json={"name": "madrid_real_estate.csv", "format": "csv"})
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["name"] == "madrid_real_estate.csv"
    assert created["version"] == 1

    list_response = client.get("/datasets")
    assert list_response.status_code == 200
    datasets = list_response.json()
    assert any(d["id"] == created["id"] for d in datasets)

    detail_response = client.get(f"/datasets/{created['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == created["id"]


def test_get_missing_dataset_404(client, db_session) -> None:
    response = client.get("/datasets/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
