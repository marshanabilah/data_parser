import asyncio
import secrets
from typing import Protocol, Sequence, Tuple

from link_extractor import ProductFetchError, UnsafeUrlError
from models import ExtractedProduct, OrderDraft, OrderPreview, SavedOrder, SubmittedLink
from order_parser import OrderMessageInterpreter, OrderParseError, parse_order_message


class OrderPreparationError(ValueError):
    """Raised when a submission cannot safely become a complete preview."""


class ProductExtractor(Protocol):
    async def extract(self, link: str) -> ExtractedProduct:
        ...


class OrderRepository(Protocol):
    def find_duplicate_links(self, links: Sequence[str]) -> frozenset[str]:
        ...

    def save_orders(
        self,
        drafts: Sequence[OrderDraft],
        submission_token: str,
    ) -> Tuple[SavedOrder, ...]:
        ...


class OrderIntakeService:
    def __init__(
        self,
        interpreter: OrderMessageInterpreter,
        extractor: ProductExtractor,
        repository: OrderRepository,
        token_factory=lambda: secrets.token_urlsafe(12),
    ) -> None:
        self._interpreter = interpreter
        self._extractor = extractor
        self._repository = repository
        self._token_factory = token_factory

    async def prepare(self, message: str) -> OrderPreview:
        try:
            submitted = await asyncio.to_thread(
                parse_order_message,
                message,
                self._interpreter,
            )
        except OrderParseError as exc:
            raise OrderPreparationError(str(exc)) from exc

        drafts = []
        for link in submitted:
            drafts.append(await self._enrich(link))

        duplicate_links = await asyncio.to_thread(
            self._repository.find_duplicate_links,
            [draft.link for draft in drafts],
        )
        warned_drafts = tuple(
            OrderDraft(
                dn_line=draft.dn_line,
                link=draft.link,
                items=draft.items,
                nominal=draft.nominal,
                warnings=(
                    *draft.warnings,
                    *(
                        ("This link already exists in Orders",)
                        if draft.link in duplicate_links
                        else ()
                    ),
                ),
            )
            for draft in drafts
        )
        return OrderPreview(
            submission_token=self._token_factory(),
            orders=warned_drafts,
        )

    async def confirm(self, preview: OrderPreview) -> Tuple[SavedOrder, ...]:
        return await asyncio.to_thread(
            self._repository.save_orders,
            preview.orders,
            preview.submission_token,
        )

    async def _enrich(self, submitted: SubmittedLink) -> OrderDraft:
        warnings = []
        try:
            extracted = await self._extractor.extract(submitted.link)
        except UnsafeUrlError as exc:
            raise OrderPreparationError(
                "A submitted link is not a safe public page"
            ) from exc
        except ProductFetchError as exc:
            if not submitted.items_override:
                raise OrderPreparationError(
                    "Items could not be determined; add an Items: override"
                ) from exc
            extracted = ExtractedProduct(items=None, nominal=None)
            warnings.append("Product page could not be read; manual item was used")

        items = submitted.items_override or extracted.items
        nominal = submitted.nominal_override or extracted.nominal
        if not items:
            raise OrderPreparationError(
                "Items could not be determined; add an Items: override"
            )
        if nominal is None:
            warnings.append("Nominal is missing")

        return OrderDraft(
            dn_line=submitted.dn_line,
            link=submitted.link,
            items=items,
            nominal=nominal,
            warnings=tuple(warnings),
        )
