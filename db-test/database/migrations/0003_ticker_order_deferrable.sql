-- M6: allow deterministic display-order swaps when a new fund sorts between existing funds.
ALTER TABLE public.app_ticker_config
    DROP CONSTRAINT app_ticker_config_display_order_key;
ALTER TABLE public.app_ticker_config
    ADD CONSTRAINT uq_app_ticker_display_order UNIQUE (display_order)
    DEFERRABLE INITIALLY DEFERRED;
