from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, StringConstraints, model_validator


ProductStatus = Literal["draft", "pending", "private", "publish", "future"]
StockStatus = Literal["instock", "outofstock", "onbackorder"]
CatalogVisibility = Literal["visible", "catalog", "search", "hidden"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Price = Annotated[str, StringConstraints(max_length=32, pattern=r"^(?:[0-9]+(?:\.[0-9]+)?)?$")]


class ProductTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: PositiveInt


class ProductImage(ProductTerm):
    alt: str | None = Field(default=None, max_length=500)


class ProductAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = Field(default=None, ge=0)
    name: Name | None = None
    visible: bool | None = None
    variation: bool | None = None
    position: int | None = Field(default=None, ge=0)
    options: list[Name] = Field(max_length=100)

    @model_validator(mode="after")
    def require_identifier(self):
        if not self.id and not self.name:
            raise ValueError("A product attribute needs an ID or a name.")
        return self


class ProductChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    description: str | None = Field(default=None, max_length=200_000)
    short_description: str | None = Field(default=None, max_length=100_000)
    regular_price: Price | None = None
    sale_price: Price | None = None
    sku: str | None = Field(default=None, max_length=255)
    status: ProductStatus | None = None
    stock_status: StockStatus | None = None
    manage_stock: bool | None = None
    stock_quantity: int | None = Field(default=None, ge=-2_147_483_648, le=2_147_483_647)
    catalog_visibility: CatalogVisibility | None = None
    featured: bool | None = None
    categories: list[ProductTerm] | None = Field(default=None, max_length=100)
    tags: list[ProductTerm] | None = Field(default=None, max_length=100)
    images: list[ProductImage] | None = Field(default=None, max_length=100)
    attributes: list[ProductAttribute] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def reject_null_fields(self):
        for field in self.model_fields_set - {"stock_quantity"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null.")
        return self


class NewProduct(ProductChanges):
    name: Name
    status: ProductStatus = "draft"
