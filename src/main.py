import asyncio
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(f"{PROJECT_ROOT}/.env")

# Базовый URL вашего API бэкенда
API_BASE_URL = os.getenv("API_URL")
if API_BASE_URL is None:
    raise ValueError("API_URL not set")

# Метка для отображения фильтра None
NONE_LABEL = "None"

# Сопоставление русскоязычного названия режима расчета с эндпоинтами API
FETCH_MODE_TO_CALC_TYPE = {
    "Все (Базовая цена)": None,  # Или "base", если на бэкенде есть базовый тип
    "Цена + Налог": "with_tax",
    "Цена + Налог + Депозит": "with_tax_and_deposit",
    "Цена + Налог + Депозит + Риелтор": "with_tax_deposit_and_realtor",
    "Цена + Депозит": "with_deposit",
    "Цена + Депозит + Риелтор": "with_deposit_and_realtor",
}


# ------------------------------------------------------------------------------
# 1. ВЗАИМОДЕЙСТВИЕ С API (HTTP REQUESTS)
# ------------------------------------------------------------------------------


def parse_dto_dict(item: dict) -> dict:
    """Нормализует словарь, полученный из JSON-ответа API."""
    data = item.copy()

    # Парсинг вложенного объекта price
    price_obj = data.pop("price", None) or {}
    data["price_amount"] = price_obj.get("amount")
    currency = price_obj.get("currency")
    if isinstance(currency, dict):
        data["currency"] = currency.get("value", "EUR")
    else:
        data["currency"] = currency or "EUR"

    # Парсинг вложенных объектов tax, deposit, realtor_service
    tax_obj = data.pop("tax", None)
    deposit_obj = data.pop("deposit", None)
    realtor_obj = data.pop("realtor_service", None)

    data["tax_amount"] = tax_obj.get("amount") if tax_obj else 0.0
    data["deposit_amount"] = deposit_obj.get("amount") if deposit_obj else 0.0
    data["realtor_amount"] = realtor_obj.get("amount") if realtor_obj else 0.0

    if "is_has_been_realtor_services" not in data:
        data["is_has_been_realtor_services"] = None

    return data


def load_data_from_api(
    service_key: str, fetch_mode: str, source_name: str = ""
) -> pd.DataFrame:
    """Делает HTTP GET запрос к рабочей ручке API с query-параметрами."""
    # Формируем корректный URL: http://localhost:8000/api/v1/house-offers/{service_key}
    url = f"{API_BASE_URL}/house-offers/{service_key}"

    # Передаем query-параметр calc_type, если он выбран
    calc_type = FETCH_MODE_TO_CALC_TYPE.get(fetch_mode)
    params = {}
    if calc_type:
        params["calc_type"] = calc_type

    try:
        response = httpx.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        raw_data = response.json()
    except httpx.HTTPError as err:
        st.error(f"Ошибка при запросе к API ({err.request.url}): {err}")
        return pd.DataFrame()

    if not raw_data:
        return pd.DataFrame()

    parsed_dicts = [parse_dto_dict(item) for item in raw_data]
    df = pd.DataFrame(parsed_dicts)

    if source_name:
        df["aggregator"] = source_name

    if "created_at" in df.columns:
        df["created_at"] = pd.to_datetime(df["created_at"])

    return df


async def trigger_grab_offers_api(service_key: str, use_cache: bool):
    """Делает HTTP POST запрос к ручке API для запуска скрапинга."""
    url = f"{API_BASE_URL}/house-offers/{service_key}/grab"
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(url, params={"cache": use_cache})
        response.raise_for_status()


# ------------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СЕКТОРОВ
# ------------------------------------------------------------------------------


def get_sector_options_and_defaults(df: pd.DataFrame):
    """Формирует полный список опций секторов, включая None."""
    unique_sectors = set()

    if "sector" in df.columns:
        for val in df["sector"].dropna().unique():
            try:
                unique_sectors.add(int(val))
            except (ValueError, TypeError):
                pass

    sorted_numeric = sorted(list(unique_sectors))
    options = [str(s) for s in sorted_numeric]

    if "sector" in df.columns and df["sector"].isna().any():
        options.append(NONE_LABEL)

    return options


def format_sector_label(option: str) -> str:
    """Форматирует название пункта в выпадающем списке."""
    if option == NONE_LABEL:
        return "Не Бухарест (None)"
    return f"Сектор {option}"


def filter_df_by_sectors(df: pd.DataFrame, selected_sectors: list) -> pd.DataFrame:
    """Фильтрует DataFrame по выбранным секторам с учетом 'None'."""
    if not selected_sectors or df.empty or "sector" not in df.columns:
        return df.iloc[0:0]

    has_none = NONE_LABEL in selected_sectors
    numeric_selected = [
        int(s) for s in selected_sectors if s != NONE_LABEL and str(s).isdigit()
    ]

    mask_numeric = df["sector"].isin(numeric_selected)
    mask_none = df["sector"].isna() if has_none else pd.Series(False, index=df.index)

    return df[mask_numeric | mask_none]


# ------------------------------------------------------------------------------
# 2. ФУНКЦИЯ ОТОБРАЖЕНИЯ ДАШБОРДА ДЛЯ КОНКРЕТНОГО СЕРВИСА
# ------------------------------------------------------------------------------


def render_service_dashboard(service_key: str, service_title: str):
    """Отрисовывает интерфейс для выбранного сервиса (работа с API)."""

    with st.expander("⚙️ Управление и обновление данных", expanded=False):
        c1, c2 = st.columns([1, 2])
        with c1:
            use_cache = st.checkbox(
                "Использовать кэш HTML", value=True, key=f"{service_key}_use_cache"
            )
            if st.button("🌐 Запустить grab_offers()", key=f"{service_key}_grab_btn"):
                with st.spinner("Запрос к API на сбор данных..."):
                    try:
                        asyncio.run(trigger_grab_offers_api(service_key, use_cache))
                        st.success("Данные успешно обновлены!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Не удалось запустить сбор данных: {e}")

        with c2:
            fetch_mode = st.selectbox(
                "Режим расчета цены (Вызов API):",
                options=list(FETCH_MODE_TO_CALC_TYPE.keys()),
                key=f"{service_key}_fetch_mode",
            )

    df_raw = load_data_from_api(
        service_key, fetch_mode, source_name=service_title
    )

    if df_raw.empty:
        st.info("В базе данных этого сервиса пока нет записей. Запустите сбор данных выше.")
        return

    # --- Панель фильтров ---
    st.markdown("#### 🔍 Фильтры предложений")
    f_col1, f_col2, f_col3 = st.columns(3)

    min_price = (
        float(df_raw["price_amount"].min())
        if df_raw["price_amount"].notna().any()
        else 0.0
    )
    max_price = (
        float(df_raw["price_amount"].max())
        if df_raw["price_amount"].notna().any()
        else 1000000.0
    )

    with f_col1:
        price_range = st.slider(
            "Диапазон цены (€)",
            min_value=min_price,
            max_value=max_price,
            value=(min_price, max_price),
            key=f"{service_key}_price_range",
        )

    with f_col2:
        sector_options = get_sector_options_and_defaults(df_raw)
        selected_sectors = st.multiselect(
            "Сектор / Район",
            options=sector_options,
            default=sector_options,
            format_func=format_sector_label,
            key=f"{service_key}_sectors",
        )

    with f_col3:
        is_owner_filter = st.selectbox(
            "Продавец",
            options=["Все", "Только собственник", "Только риелторы/агентства"],
            key=f"{service_key}_is_owner",
        )

    # Фильтрация
    df = df_raw[
        (df_raw["price_amount"] >= price_range[0])
        & (df_raw["price_amount"] <= price_range[1])
    ]
    df = filter_df_by_sectors(df, selected_sectors)

    if is_owner_filter == "Только собственник":
        df = df[df["is_owner"] == True]
    elif is_owner_filter == "Только риелторы/агентства":
        df = df[df["is_owner"] == False]

    st.divider()

    # --- Метрики (KPI) ---
    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    kpi1.metric("Найдено предложений", len(df))

    avg_price = df["price_amount"].mean() if not df.empty else 0
    kpi2.metric("Средняя цена (€)", f"€{avg_price:,.2f}")

    owners_count = (
        df["is_owner"].sum() if "is_owner" in df.columns and not df.empty else 0
    )
    kpi3.metric("От собственников", int(owners_count))

    realtor_count = (
        len(df) - df["is_owner"].sum() if "is_owner" in df.columns and not df.empty else 0
    )
    kpi4.metric("От риелторов", int(realtor_count))

    realtor_price_indicated_count = (
        df["is_has_been_realtor_services"].sum()
        if "is_has_been_realtor_services" in df.columns
        and df["is_has_been_realtor_services"].notna().any()
        else 0
    )
    kpi5.metric("Указали цену услуг риелтора", int(realtor_price_indicated_count))

    # --- Таблица данных ---
    st.subheader("📋 Данные объявлений")
    st.data_editor(
        df,
        column_config={
            "aggregator": "Агрегатор",
            "id": "ID",
            "id_url": "ID URL",
            "url": st.column_config.LinkColumn(
                "Ссылка на объявление", display_text="Открыть 🔗"
            ),
            "sector": st.column_config.NumberColumn("Сектор", format="%d"),
            "is_owner": st.column_config.CheckboxColumn("Собственник"),
            "is_has_been_realtor_services": st.column_config.CheckboxColumn(
                "Была указана цена услуг риелтора"
            ),
            "created_at": st.column_config.DatetimeColumn(
                "Дата создания", format="DD.MM.YYYY HH:mm"
            ),
            "price_amount": st.column_config.NumberColumn(
                "Итоговая цена (€)", format="€%.2f"
            ),
            "currency": "Валюта",
            "tax_amount": st.column_config.NumberColumn(
                "Налог (€)", format="€%.2f"
            ),
            "deposit_amount": st.column_config.NumberColumn(
                "Залог (€)", format="€%.2f"
            ),
            "realtor_amount": st.column_config.NumberColumn(
                "Комиссия риелтора (€)", format="€%.2f"
            ),
        },
        hide_index=True,
        use_container_width=True,
        disabled=True,
        key=f"{service_key}_data_editor",
    )

    # --- Графики и Аналитика ---
    st.divider()
    st.subheader("📊 Аналитика")

    sub_tab1, sub_tab2, sub_tab3 = st.tabs(
        ["Секторы и Цены", "Распределение и Корреляция", "Динамика публикаций"]
    )

    with sub_tab1:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Средняя цена по секторам")
            if not df.empty:
                df_sector_plot = df.copy()
                df_sector_plot["sector_label"] = df_sector_plot["sector"].apply(
                    lambda x: "None" if pd.isna(x) else f"Сектор {int(x)}"
                )

                sector_agg = (
                    df_sector_plot.groupby("sector_label")["price_amount"]
                    .mean()
                    .reset_index()
                    .sort_values("price_amount", ascending=False)
                )
                fig_bar = px.bar(
                    sector_agg,
                    x="sector_label",
                    y="price_amount",
                    color="price_amount",
                    labels={
                        "sector_label": "Сектор",
                        "price_amount": "Средняя цена (€)",
                    },
                    color_continuous_scale="Viridis",
                )
                st.plotly_chart(
                    fig_bar, use_container_width=True, key=f"{service_key}_fig_bar"
                )

        with col2:
            st.markdown("##### Соотношение: Собственник vs Риелтор")
            if not df.empty and "is_owner" in df.columns:
                owner_df = (
                    df["is_owner"]
                    .map({True: "Собственник", False: "Риелтор / Агентство"})
                    .fillna("Не указано")
                    .value_counts()
                    .reset_index()
                )
                owner_df.columns = ["Тип", "Количество"]
                fig_pie = px.pie(owner_df, values="Количество", names="Тип", hole=0.4)
                st.plotly_chart(
                    fig_pie, use_container_width=True, key=f"{service_key}_fig_pie"
                )

    with sub_tab2:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Распределение цен предложений (Гистограмма)")
            if not df.empty:
                fig_hist = px.histogram(
                    df,
                    x="price_amount",
                    nbins=20,
                    labels={"price_amount": "Цена (€)"},
                    title="Распределение стоимости предложений",
                )
                st.plotly_chart(
                    fig_hist, use_container_width=True, key=f"{service_key}_fig_hist"
                )

        with col2:
            st.markdown("##### Разброс цен по секторам (Box Plot)")
            if not df.empty:
                df_box_plot = df.copy()
                df_box_plot["sector_label"] = df_box_plot["sector"].apply(
                    lambda x: "None" if pd.isna(x) else f"Сектор {int(x)}"
                )
                fig_box = px.box(
                    df_box_plot,
                    x="sector_label",
                    y="price_amount",
                    color="sector_label",
                    labels={"sector_label": "Сектор", "price_amount": "Цена (€)"},
                )
                st.plotly_chart(
                    fig_box, use_container_width=True, key=f"{service_key}_fig_box"
                )

    with sub_tab3:
        st.markdown("##### Динамика появления объявлений во времени")
        if not df.empty and df["created_at"].notna().any():
            time_df = (
                df.groupby(df["created_at"].dt.date)["price_amount"]
                .agg(["count", "mean"])
                .reset_index()
            )
            fig_line = px.line(
                time_df,
                x="created_at",
                y="mean",
                markers=True,
                labels={
                    "created_at": "Дата создания",
                    "mean": "Средняя цена (€)",
                },
                title="Динамика средней стоимости объявлений по дням",
            )
            st.plotly_chart(
                fig_line, use_container_width=True, key=f"{service_key}_fig_line"
            )
        else:
            st.info("Нет данных о датах создания объявлений.")


# ------------------------------------------------------------------------------
# 3. ФУНКЦИЯ ДЛЯ ОБЪЕДИНЕННОГО СРАВНИТЕЛЬНОГО ДАШБОРДА
# ------------------------------------------------------------------------------


def render_combined_dashboard(services_config: dict):
    """Отрисовывает общую таблицу и сравнительную статистику по API."""

    st.subheader("🌐 Сравнение и Объединенные данные")

    col_ctrl1, col_ctrl2 = st.columns([1, 2])

    with col_ctrl1:
        selected_sources = st.multiselect(
            "Выберите агрегаторы для сравнения:",
            options=list(services_config.keys()),
            default=list(services_config.keys()),
            format_func=lambda k: services_config[k]["title"],
            key="combined_sources_select",
        )

    with col_ctrl2:
        fetch_mode = st.selectbox(
            "Режим расчета цены:",
            options=list(FETCH_MODE_TO_CALC_TYPE.keys()),
            key="combined_fetch_mode",
        )

    if not selected_sources:
        st.warning("Пожалуйста, выберите хотя бы один агрегатор.")
        return

    dfs = []
    for s_key in selected_sources:
        cfg = services_config[s_key]
        try:
            df = load_data_from_api(
                service_key=s_key,
                fetch_mode=fetch_mode,
                source_name=cfg["title"],
            )
            if not df.empty:
                dfs.append(df)
        except Exception as e:
            st.error(f"Ошибка при загрузке данных из {cfg['title']}: {e}")

    if not dfs:
        st.info("Нет данных в БД выбранных сервисов.")
        return

    combined_df = pd.concat(dfs, ignore_index=True)

    # --- Фильтры для объединенных данных ---
    st.markdown("#### 🔍 Фильтры общих данных")
    f_col1, f_col2, f_col3 = st.columns(3)

    min_p = float(combined_df["price_amount"].min())
    max_p = float(combined_df["price_amount"].max())

    with f_col1:
        price_range = st.slider(
            "Диапазон цены (€)",
            min_value=min_p,
            max_value=max_p,
            value=(min_p, max_p),
            key="combined_price_range",
        )

    with f_col2:
        sector_options = get_sector_options_and_defaults(combined_df)
        selected_sectors = st.multiselect(
            "Сектор / Район",
            options=sector_options,
            default=sector_options,
            format_func=format_sector_label,
            key="combined_sectors",
        )

    with f_col3:
        is_owner_filter = st.selectbox(
            "Продавец",
            options=["Все", "Только собственник", "Только риелторы/агентства"],
            key="combined_is_owner",
        )

    df_filtered = combined_df[
        (combined_df["price_amount"] >= price_range[0])
        & (combined_df["price_amount"] <= price_range[1])
    ]

    df_filtered = filter_df_by_sectors(df_filtered, selected_sectors)

    if is_owner_filter == "Только собственник":
        df_filtered = df_filtered[df_filtered["is_owner"] == True]
    elif is_owner_filter == "Только риелторы/агентства":
        df_filtered = df_filtered[df_filtered["is_owner"] == False]

    st.divider()

    # --- Общие KPI по источникам ---
    st.markdown("#### 📈 Общая статистика по источникам")
    cols = st.columns(len(selected_sources) + 1)

    cols[0].metric("Всего объявлений", len(df_filtered))

    for i, s_key in enumerate(selected_sources):
        title = services_config[s_key]["title"]
        sub_df = df_filtered[df_filtered["aggregator"] == title]
        avg_p = sub_df["price_amount"].mean() if not sub_df.empty else 0
        cols[i + 1].metric(
            f"{title}",
            f"{len(sub_df)} шт.",
            f"Ср: €{avg_p:,.0f}" if avg_p else "Нет данных",
        )


    # --- Общий датафрейм ---
    st.subheader("📋 Сводная таблица (Все агрегаторы)")
    st.data_editor(
        df_filtered,
        column_config={
            "aggregator": "Агрегатор",
            "id": "ID",
            "id_url": "ID URL",
            "url": st.column_config.LinkColumn(
                "Ссылка на объявление", display_text="Открыть 🔗"
            ),
            "sector": st.column_config.NumberColumn("Сектор", format="%d"),
            "is_owner": st.column_config.CheckboxColumn("Собственник"),
            "is_has_been_realtor_services": st.column_config.CheckboxColumn(
                "Риелторские услуги"
            ),
            "created_at": st.column_config.DatetimeColumn(
                "Дата создания", format="DD.MM.YYYY HH:mm"
            ),
            "price_amount": st.column_config.NumberColumn(
                "Итоговая цена (€)", format="€%.2f"
            ),
            "currency": "Валюта",
            "tax_amount": st.column_config.NumberColumn(
                "Налог (€)", format="€%.2f"
            ),
            "deposit_amount": st.column_config.NumberColumn(
                "Залог (€)", format="€%.2f"
            ),
            "realtor_amount": st.column_config.NumberColumn(
                "Комиссия риелтора (€)", format="€%.2f"
            ),
        },
        hide_index=True,
        use_container_width=True,
        disabled=True,
        key="combined_data_editor",
    )

    # --- Сравнительная графическая аналитика ---
    st.divider()
    st.subheader("📊 Сравнительный анализ агрегаторов")

    comp_tab1, comp_tab2, comp_tab3 = st.tabs(
        [
            "Сравнение цен (Box Plot & Hist)",
            "Секторы по агрегаторам",
            "Доля рынка & Собственники",
        ]
    )

    with comp_tab1:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Разброс цен по агрегаторам")
            fig_box = px.box(
                df_filtered,
                x="aggregator",
                y="price_amount",
                color="aggregator",
                labels={"aggregator": "Агрегатор", "price_amount": "Цена (€)"},
                title="Сравнение диапазона и медианных цен",
            )
            st.plotly_chart(fig_box, use_container_width=True, key="comp_fig_box")

        with col2:
            st.markdown("##### Сравнение распределения цен")
            fig_hist = px.histogram(
                df_filtered,
                x="price_amount",
                color="aggregator",
                barmode="overlay",
                nbins=25,
                labels={"price_amount": "Цена (€)", "aggregator": "Источник"},
                title="Наложение гистограмм цен",
            )
            st.plotly_chart(fig_hist, use_container_width=True, key="comp_fig_hist")

    with comp_tab2:
        st.markdown("##### Средняя цена по секторам в разрезе агрегаторов")
        if not df_filtered.empty:
            df_comp_sector = df_filtered.copy()
            df_comp_sector["sector_label"] = df_comp_sector["sector"].apply(
                lambda x: "None" if pd.isna(x) else f"Сектор {int(x)}"
            )

            sec_agg = (
                df_comp_sector.groupby(["sector_label", "aggregator"])["price_amount"]
                .mean()
                .reset_index()
            )
            fig_group_bar = px.bar(
                sec_agg,
                x="sector_label",
                y="price_amount",
                color="aggregator",
                barmode="group",
                labels={
                    "sector_label": "Сектор",
                    "price_amount": "Средняя цена (€)",
                    "aggregator": "Агрегатор",
                },
                title="Сравнение стоимости жилья по секторам между площадками",
            )
            st.plotly_chart(
                fig_group_bar, use_container_width=True, key="comp_fig_group_bar"
            )

    with comp_tab3:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("##### Распределение количества объявлений")
            agg_counts = df_filtered["aggregator"].value_counts().reset_index()
            agg_counts.columns = ["Агрегатор", "Количество"]
            fig_pie_agg = px.pie(
                agg_counts,
                values="Количество",
                names="Агрегатор",
                hole=0.4,
                title="Доля объявлений каждого сервиса",
            )
            st.plotly_chart(
                fig_pie_agg, use_container_width=True, key="comp_fig_pie_agg"
            )

        with col2:
            st.markdown("##### Доля собственников по агрегаторам")
            if "is_owner" in df_filtered.columns:
                owner_agg = (
                    df_filtered.groupby(["aggregator", "is_owner"])
                    .size()
                    .reset_index(name="count")
                )
                owner_agg["Тип"] = owner_agg["is_owner"].map(
                    {True: "Собственник", False: "Риелтор"}
                )
                fig_owner_bar = px.bar(
                    owner_agg,
                    x="aggregator",
                    y="count",
                    color="Тип",
                    barmode="stack",
                    title="Собственники vs Риелторы по площадкам",
                    labels={"count": "Количество", "aggregator": "Агрегатор"},
                )
                st.plotly_chart(
                    fig_owner_bar, use_container_width=True, key="comp_fig_owner_bar"
                )


# ------------------------------------------------------------------------------
# 4. ГЛАВНАЯ СТРАНИЦА И ВКЛАДКИ
# ------------------------------------------------------------------------------

st.set_page_config(
    page_title="Аналитика Недвижимости",
    page_icon="🏠",
    layout="wide",
)

st.title("🏠 Мониторинг и Сравнение предложений недвижимости")

SERVICES = {
    "imobiliare": {
        "title": "🏛️ Imobiliare",
    },
    "otodom": {
        "title": "🏠 Otodom",
    },
    "olx": {
        "title": "🏠 Olx",
    },
}

tab_titles = ["📊 Сравнение (Все)"] + [s["title"] for s in SERVICES.values()]
main_tabs = st.tabs(tab_titles)

with main_tabs[0]:
    render_combined_dashboard(SERVICES)

for tab, (service_key, service_cfg) in zip(main_tabs[1:], SERVICES.items()):
    with tab:
        try:
            render_service_dashboard(
                service_key=service_key,
                service_title=service_cfg["title"],
            )
        except Exception as e:
            st.error(
                f"Ошибка при обработке сервиса {service_cfg['title']}: {e}"
            )