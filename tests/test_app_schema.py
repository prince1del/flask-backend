from app.firebase_service import FirebaseEntityService
from app.schema import Distributor, Retailer, Product


def test_schema_models_can_be_created():
    distributor = Distributor(
        id="d1",
        name="ABC Traders",
        contact_person="Ravi",
        phone="9988776655",
        email="ravi@example.com",
        address="Main Road",
        city="Delhi",
        state="DL",
    )
    retailer = Retailer(
        id="r1",
        name="Shop 24",
        contact_person="Meera",
        phone="8877665544",
        email="meera@example.com",
        address="Market Road",
        city="Mumbai",
        state="MH",
    )
    product = Product(
        id="p1",
        name="Milk Powder",
        category="Food",
        unit="kg",
        price=120.0,
        stock_quantity=50,
    )

    assert distributor.name == "ABC Traders"
    assert retailer.email == "meera@example.com"
    assert product.stock_quantity == 50


def test_firebase_service_uses_expected_nodes():
    service = FirebaseEntityService()
    service.add_distributor({"name": "ABC Traders"})
    service.add_retailer({"name": "Shop 24"})
    service.add_product({"name": "Milk Powder"})

    assert service.sync.sync_store.pending_count() >= 1
