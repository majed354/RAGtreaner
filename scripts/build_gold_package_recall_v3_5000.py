"""Build a 5000-case gold benchmark for Saudi legal RAG package recall.

The benchmark is external evaluation data. The RAG service receives only the
question text; expected regulations and exclusions are used offline by the
scoring script.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_gold_package_recall_v2_1000 import (
    AR_STOPWORDS,
    BY_REGULATION_DIR,
    DOMAIN_DISTRACTORS,
    REGULATIONS_PATH,
    ROOT,
    article_score,
    article_text,
    choose_articles,
    domain_for,
    exclusions_for,
    load_jsonl,
    load_regulation_articles,
    make_article_case,
    normalize_space,
    title_alias,
)


TARGET_CASES = 5000
V2_CASES_PATH = ROOT / "data" / "eval" / "gold_package_recall_v2_1000" / "gold_package_recall_1000_v2.jsonl"
OUT_DIR = ROOT / "data" / "eval" / "gold_package_recall_v3_5000"
CASES_PATH = OUT_DIR / "gold_package_recall_5000_v3.jsonl"
MANIFEST_PATH = OUT_DIR / "manifest.json"
README_PATH = OUT_DIR / "README.md"

SPLIT_CYCLE = ("dev", "regression", "heldout", "regression", "dev", "heldout", "regression", "heldout")

V3_COMPANION_BY_SLUG: dict[str, list[str]] = {
    "labor-law": [
        "labor-implementing-regulation",
        "wage-protection-rules",
        "labor-contract-documentation-rules",
        "labor-violations-penalties-table",
    ],
    "labor-implementing-regulation": ["labor-law", "labor-violations-penalties-table"],
    "wage-protection-rules": ["labor-law", "labor-implementing-regulation"],
    "labor-contract-documentation-rules": ["labor-law", "labor-implementing-regulation"],
    "labor-violations-penalties-table": ["labor-law", "labor-implementing-regulation"],
    "nzam-altamynat-alajtmaayh": ["labor-law"],
    "nzam-altamyn-dd-altatl-an-alaml": ["labor-law", "nzam-altamynat-alajtmaayh"],
    "e-commerce-law": ["ecommerce-implementing-regulation", "personal-data-protection-law"],
    "ecommerce-implementing-regulation": ["e-commerce-law"],
    "commercial-fraud-law": ["e-commerce-law"],
    "personal-data-protection-law": ["pdpl-implementing-regulation"],
    "pdpl-implementing-regulation": ["personal-data-protection-law"],
    "pdpl-transfer-regulation": ["personal-data-protection-law", "pdpl-implementing-regulation"],
    "anti-cybercrime-law": ["personal-data-protection-law", "pdpl-implementing-regulation"],
    "nzam-drybh-alqymh-almdafh": ["zatca-vat-implementing-regulation"],
    "zatca-vat-implementing-regulation": ["nzam-drybh-alqymh-almdafh"],
    "zatca-e-invoicing-bylaw": ["nzam-drybh-alqymh-almdafh", "zatca-e-invoicing-technical-controls"],
    "zatca-e-invoicing-technical-controls": ["zatca-e-invoicing-bylaw", "nzam-drybh-alqymh-almdafh"],
    "nzam-drybh-altsrfat-alaqaryh": ["civil-transactions-law", "nzam-altsjyl-alayny-llaqar"],
    "government-tenders-and-procurement-law": [
        "government-procurement-implementing-regulation",
        "procurement-conflict-of-interest-regulation",
        "procurement-conduct-ethics-regulation",
        "nzam-almrafaat-amam-dywan-almzalm",
    ],
    "government-procurement-implementing-regulation": ["government-tenders-and-procurement-law"],
    "procurement-conflict-of-interest-regulation": ["government-tenders-and-procurement-law"],
    "procurement-conduct-ethics-regulation": ["government-tenders-and-procurement-law"],
    "nzam-aliflas": ["bankruptcy-implementing-regulation", "companies-law", "labor-law"],
    "bankruptcy-implementing-regulation": ["nzam-aliflas"],
    "companies-law": ["companies-implementing-regulation"],
    "companies-implementing-regulation": ["companies-law"],
    "nzam-alswq-almalyh": [
        "cma-corporate-governance-regulations",
        "cma-continuing-obligations-rules",
        "cma-securities-offering-rules",
        "companies-law",
    ],
    "cma-corporate-governance-regulations": ["companies-law", "nzam-alswq-almalyh", "cma-continuing-obligations-rules"],
    "cma-continuing-obligations-rules": ["nzam-alswq-almalyh", "cma-corporate-governance-regulations"],
    "cma-securities-offering-rules": ["nzam-alswq-almalyh", "cma-continuing-obligations-rules"],
    "nzam-almnafsh": ["companies-law"],
    "nzam-alamtyaz-altjary": ["nzam-alsjl-altjary", "nzam-alasmaa-altjaryh", "nzam-alalamat-altjaryh"],
    "nzam-alwkalat-altjaryh": ["nzam-almhakm-altjaryh", "civil-transactions-law"],
    "nzam-alawraq-altjaryh": ["law-of-evidence", "nzam-almhakm-altjaryh", "execution-law"],
    "nzam-alalamat-altjaryh": ["qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"],
    "nzam-alasmaa-altjaryh": ["nzam-alsjl-altjary", "nzam-alalamat-altjaryh"],
    "nzam-alsjl-altjary": ["nzam-alasmaa-altjaryh", "companies-law"],
    "nzam-mkafhh-altstr": ["companies-law", "nzam-alsjl-altjary"],
    "civil-transactions-law": ["law-of-evidence"],
    "law-of-evidence": ["law-of-sharia-procedure"],
    "law-of-sharia-procedure": ["law-of-evidence"],
    "nzam-almhakm-altjaryh": ["law-of-evidence", "nzam-altkalyf-alqdayyh"],
    "execution-law": ["execution-implementing-regulation", "law-of-evidence"],
    "execution-implementing-regulation": ["execution-law", "law-of-evidence"],
    "nzam-althkym": ["execution-law", "law-of-evidence"],
    "electronic-transactions-law": ["law-of-evidence"],
    "nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth": ["real-estate-brokerage-law", "civil-transactions-law"],
    "real-estate-brokerage-law": ["civil-transactions-law", "law-of-evidence"],
    "nzam-altsjyl-alayny-llaqar": ["civil-transactions-law", "law-of-evidence"],
    "nzam-ttbyq-kwd-albnaa-alsawdy": ["civil-transactions-law", "law-of-evidence"],
    "nzam-tsnyf-almqawlyn": ["government-tenders-and-procurement-law"],
    "nzam-dman-alhqwq-balamwal-almnqwlh": ["civil-transactions-law", "execution-law"],
    "nzam-alrhn-altjary": ["civil-transactions-law", "nzam-almhakm-altjaryh"],
    "nzam-mraqbh-albnwk": ["nzam-albnk-almrkzy-alsawdy"],
    "nzam-almdfwaat-wkhdmatha": ["nzam-albnk-almrkzy-alsawdy"],
    "nzam-mraqbh-shrkat-altmwyl": ["nzam-albnk-almrkzy-alsawdy", "civil-transactions-law"],
    "nzam-mraqbh-shrkat-altamyn-altaawny": ["nzam-aldman-alshy-altaawny"],
    "anti-money-laundering-law": ["nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh"],
    "copyright-law": ["law-of-evidence", "nzam-almhakm-altjaryh"],
    "communications-and-information-technology-law": ["personal-data-protection-law"],
    "nzam-alialam-almryy-walmsmwa": ["nzam-almtbwaat-walnshr"],
    "nzam-alajhzh-walmstlzmat-altbyh": ["nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
    "nzam-alghdhaa": ["nzam-alhyyh-alaamh-llghdhaa-waldwaa", "commercial-fraud-law"],
    "nzam-mntjat-altjmyl": ["nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
    "nzam-almnshat-walmsthdrat-alsydlanyh-walashbyh": ["nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
    "nzam-mzawlh-almhn-alshyh": ["alnzam-alshy", "law-of-evidence"],
    "nzam-almwssat-alshyh-alkhash": ["alnzam-alshy", "nzam-mzawlh-almhn-alshyh"],
    "personal-status-law": ["law-of-evidence"],
    "nzam-hmayh-altfl": ["personal-status-law", "protection-from-abuse-law"],
    "protection-from-abuse-law": ["nzam-hmayh-altfl", "criminal-procedure-law"],
    "nzam-mkafhh-jrymh-althrsh": ["criminal-procedure-law", "law-of-evidence"],
    "nzam-alahdath": ["criminal-procedure-law", "nzam-hmayh-altfl"],
    "nzam-albyyh": ["law-of-evidence"],
    "nzam-ijraaat-altrakhys-albldyh": ["nzam-almhakm-altjaryh"],
}


SCENARIO_FAMILIES: list[dict[str, Any]] = [
    {
        "family_id": "labor_private_establishment_contract_wage_qiwa_noncompete",
        "domain": "labor",
        "scenario": "منشأة خاصة لديها سعوديون وغير سعوديين، أخرت الرواتب شهرين، مددت فترة التجربة بالبريد، أنهت عقداً محدد المدة قبل نهايته، طلبت عملاً عن بعد دون توثيق، لم توثق بعض العقود في قوى، ووضعت شرط عدم منافسة واسعاً",
        "core": ["labor-law"],
        "companions": ["labor-implementing-regulation", "wage-protection-rules", "labor-contract-documentation-rules", "labor-violations-penalties-table"],
        "optional": ["law-of-evidence", "nzam-altamynat-alajtmaayh"],
        "excluded": ["government-tenders-and-procurement-law", "civil-transactions-law"],
    },
    {
        "family_id": "labor_harassment_retaliation_dues",
        "domain": "labor",
        "scenario": "موظفة أبلغت عن تحرش في بيئة العمل ثم فصلت، وتأخر صاحب العمل في راتبها الأخير ومكافأة نهاية الخدمة، وتوجد رسائل وشهود على البلاغ",
        "core": ["labor-law", "nzam-mkafhh-jrymh-althrsh"],
        "companions": ["labor-implementing-regulation", "labor-violations-penalties-table", "workplace-behavioral-misconduct-controls", "law-of-evidence"],
        "optional": ["criminal-procedure-law"],
        "excluded": ["nzam-alkhdmh-almdnyh"],
    },
    {
        "family_id": "labor_overtime_holidays_deduction_termination",
        "domain": "labor",
        "scenario": "عامل في شركة خاصة كلف بساعات إضافية وفي أيام عطل، ثم خصم من أجره وأنهي عقده دون إشعار كاف ويطلب أجراً إضافياً وتعويضاً وتصفية حقوقه",
        "core": ["labor-law"],
        "companions": ["labor-implementing-regulation", "labor-violations-penalties-table", "wage-protection-rules"],
        "optional": ["law-of-evidence"],
        "excluded": ["government-tenders-and-procurement-law"],
    },
    {
        "family_id": "labor_injury_safety_social_insurance",
        "domain": "labor",
        "scenario": "عامل أصيب أثناء العمل بسبب ضعف وسائل السلامة، وتأخر صاحب العمل في الإبلاغ، ويطلب التعويض والحقوق التأمينية وإثبات الإصابة المهنية",
        "core": ["labor-law", "nzam-altamynat-alajtmaayh"],
        "companions": ["labor-implementing-regulation", "labor-violations-penalties-table", "law-of-evidence"],
        "optional": ["nzam-altamyn-dd-altatl-an-alaml"],
        "excluded": ["civil-transactions-law"],
    },
    {
        "family_id": "ecommerce_digital_service_not_activated_marketing_data",
        "domain": "ecommerce_consumer",
        "scenario": "مستهلك دفع لاشتراك أو دورة رقمية ولم تفعل الخدمة في الموعد، طلب إلغاء واسترداداً، ثم استخدم المتجر رقم الجوال وبيانات التسجيل في تسويق وشراكة إعلانية",
        "core": ["e-commerce-law"],
        "companions": ["ecommerce-implementing-regulation", "personal-data-protection-law", "pdpl-implementing-regulation"],
        "optional": ["pdpl-transfer-regulation"],
        "excluded": ["copyright-law"],
    },
    {
        "family_id": "ecommerce_medical_device_fraud_overcollection",
        "domain": "ecommerce_consumer",
        "scenario": "متجر إلكتروني باع جهازاً طبياً منزلياً بادعاءات علاجية غير دقيقة، رفض الاسترجاع، وطلب رقم الهوية وتاريخ الميلاد دون حاجة ظاهرة للتوصيل",
        "core": ["e-commerce-law", "commercial-fraud-law"],
        "companions": ["ecommerce-implementing-regulation", "personal-data-protection-law", "pdpl-implementing-regulation", "nzam-alajhzh-walmstlzmat-altbyh"],
        "optional": ["law-of-evidence"],
        "excluded": ["companies-law"],
    },
    {
        "family_id": "ecommerce_late_delivery_misleading_warranty_refund",
        "domain": "ecommerce_consumer",
        "scenario": "مستهلك اشترى سلعة من متجر إلكتروني، أعلن المتجر ضماناً مدى الحياة بلا شروط واضحة، تأخر التسليم أكثر من خمسة عشر يوماً ورفض الإلغاء بحجة سياسة المتجر",
        "core": ["e-commerce-law"],
        "companions": ["ecommerce-implementing-regulation", "commercial-fraud-law"],
        "optional": ["law-of-evidence"],
        "excluded": ["nzam-aliflas"],
    },
    {
        "family_id": "pdpl_health_breach_cloud_transfer_marketing",
        "domain": "privacy_data",
        "scenario": "تطبيق صحي يجمع الهوية والموقع والبيانات الصحية وبيانات الجهاز، يستضيفها لدى مزود سحابي خارج المملكة، يشاركها مع شركة تحليل تسويقي، وحدث تسرب أبلغ عنه بعد أسبوعين",
        "core": ["personal-data-protection-law", "pdpl-implementing-regulation", "pdpl-transfer-regulation"],
        "companions": ["anti-cybercrime-law"],
        "optional": ["nzam-aldman-alshy-altaawny"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "pdpl_cameras_face_recognition_retail",
        "domain": "privacy_data",
        "scenario": "مركز تجاري يستخدم كاميرات ذكية للتعرف على الوجوه وتحليل حركة الزوار وربطها بأرقام الجوال لإعلانات مخصصة دون لوحات إفصاح كافية",
        "core": ["personal-data-protection-law", "pdpl-implementing-regulation", "nzam-astkhdam-kamyrat-almraqbh-alamnyh"],
        "companions": ["anti-cybercrime-law"],
        "optional": ["communications-and-information-technology-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "pdpl_hr_employee_data_crossborder",
        "domain": "privacy_data",
        "scenario": "شركة تنقل بيانات موظفيها وملفاتهم الصحية والتقييمات الوظيفية إلى نظام موارد بشرية أجنبي، وتشارك بعضها مع شركة استشارات دون تحديد غرض أو مدة احتفاظ",
        "core": ["personal-data-protection-law", "pdpl-implementing-regulation", "pdpl-transfer-regulation"],
        "companions": ["labor-law", "law-of-evidence"],
        "optional": ["anti-cybercrime-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "zatca_vat_einvoice_pdf_credit_notes_wrong_rate",
        "domain": "tax_zatca",
        "scenario": "منشأة خاضعة للضريبة تصدر فواتير PDF عادية لا تتضمن حقول الفوترة الإلكترونية، وتطبق ضريبة القيمة المضافة بنسبة خاطئة، وتصدر إشعارات خصم يدوية بعد رد مبالغ",
        "core": ["nzam-drybh-alqymh-almdafh"],
        "companions": ["zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls"],
        "optional": ["e-commerce-law"],
        "excluded": ["electronic-transactions-law"],
    },
    {
        "family_id": "zatca_real_estate_disposal_tax_vat_boundary",
        "domain": "tax_zatca",
        "scenario": "شركة باعت عقاراً تجارياً مع تجهيزات وخدمات إدارة، واختلف الطرفان هل المستحق ضريبة تصرفات عقارية أم ضريبة قيمة مضافة أو كلاهما على أجزاء الصفقة",
        "core": ["nzam-drybh-altsrfat-alaqaryh", "nzam-drybh-alqymh-almdafh"],
        "companions": ["zatca-vat-implementing-regulation", "civil-transactions-law"],
        "optional": ["nzam-altsjyl-alayny-llaqar"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "cma_listed_company_disclosure_insider_conflict_dispute",
        "domain": "corporate_commercial",
        "scenario": "شركة مساهمة سعودية مدرجة أعلنت نتائج مالية متفائلة ثم صححت تصحيحاً جوهرياً فهبط السهم، وباع عضو مجلس أسهماً قبل التصحيح، ووافق المجلس على عقد توريد مع شركة للرئيس التنفيذي فيها مصلحة غير مباشرة",
        "core": ["companies-law", "nzam-alswq-almalyh"],
        "companions": ["companies-implementing-regulation", "cma-corporate-governance-regulations", "cma-continuing-obligations-rules", "cma-securities-offering-rules", "law-of-evidence"],
        "optional": ["civil-transactions-law"],
        "excluded": ["nzam-almhakm-altjaryh"],
    },
    {
        "family_id": "cma_capital_increase_bonus_shares_ega",
        "domain": "corporate_commercial",
        "scenario": "شركة مساهمة مدرجة تريد زيادة رأس المال بمنح أسهم ودعوة جمعية غير عادية، مع أسئلة عن سجل المساهمين والإفصاح وموافقة هيئة السوق المالية",
        "core": ["companies-law", "nzam-alswq-almalyh"],
        "companions": ["companies-implementing-regulation", "cma-corporate-governance-regulations", "cma-continuing-obligations-rules", "cma-securities-offering-rules"],
        "optional": ["law-of-evidence"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "company_llc_manager_asset_inspection_fake_dividends",
        "domain": "corporate_commercial",
        "scenario": "مدير شركة ذات مسؤولية محدودة باع أصلاً جوهرياً دون موافقة الشركاء، منع شريكاً من الاطلاع على السجلات، ووزع أرباحاً صورية رغم خسائر فعلية",
        "core": ["companies-law"],
        "companions": ["companies-implementing-regulation", "civil-transactions-law", "law-of-evidence", "nzam-almhakm-altjaryh"],
        "optional": ["nzam-aliflas"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "commercial_franchise_disclosure_register_trademark",
        "domain": "corporate_commercial",
        "scenario": "مستثمر وقع اتفاقية امتياز لتشغيل علامة مطاعم، ولم تقدم وثيقة إفصاح كافية ولم يقيد الامتياز، وظهر نزاع حول الاسم التجاري والسجل والعلامة",
        "core": ["nzam-alamtyaz-altjary"],
        "companions": ["nzam-alsjl-altjary", "nzam-alasmaa-altjaryh", "nzam-alalamat-altjaryh", "companies-law", "civil-transactions-law", "nzam-almhakm-altjaryh"],
        "optional": ["law-of-evidence"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "commercial_agency_exclusive_foreign_principal",
        "domain": "corporate_commercial",
        "scenario": "وكيل تجاري سعودي حصل على وكالة حصرية من منتج أجنبي، لم تسجل الوكالة، ثم عيّن الموكل موزعاً آخر وثار نزاع عن التعويض والاختصاص",
        "core": ["nzam-alwkalat-altjaryh"],
        "companions": ["civil-transactions-law", "nzam-almhakm-altjaryh", "nzam-alsjl-altjary"],
        "optional": ["law-of-evidence"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "competition_dominance_concentration_exclusivity",
        "domain": "corporate_commercial",
        "scenario": "منصة توصيل ذات حصة كبيرة تريد الاستحواذ على منافس ناشئ، وتفرض على المطاعم شرط حصرية يمنع التعامل مع منصات أخرى",
        "core": ["nzam-almnafsh"],
        "companions": ["companies-law"],
        "optional": ["nzam-alswq-almalyh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "procurement_conflict_collusion_grievance_award",
        "domain": "procurement_admin",
        "scenario": "جهة حكومية طرحت منافسة، وعضو لجنة الفحص قريب لمالك أحد الموردين، واتفق موردان على رفع الأسعار، ثم قدم منافس تظلماً على الترسية والتقييم",
        "core": ["government-tenders-and-procurement-law"],
        "companions": ["government-procurement-implementing-regulation", "procurement-conflict-of-interest-regulation", "procurement-conduct-ethics-regulation", "nzam-almnafsh"],
        "optional": ["nzam-almrafaat-amam-dywan-almzalm"],
        "excluded": ["civil-transactions-law"],
    },
    {
        "family_id": "procurement_subcontract_delay_government_variation",
        "domain": "procurement_admin",
        "scenario": "مقاول حكومي تأخر لأن الجهة لم تسلم الموقع وأصدرت أوامر إيقاف، ثم تعاقد من الباطن دون موافقة مكتوبة وطلب تمديداً وتعويضاً",
        "core": ["government-tenders-and-procurement-law"],
        "companions": ["government-procurement-implementing-regulation", "procurement-conduct-ethics-regulation"],
        "optional": ["nzam-almrafaat-amam-dywan-almzalm"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "civil_electronic_contract_whatsapp_invoice_evidence",
        "domain": "civil_evidence_procedure",
        "scenario": "مطالبة تجارية مبنية على عرض سعر وقبول عبر واتساب وبريد إلكتروني وفواتير، والمدين ينكر التعاقد والتوقيع الإلكتروني وقيمة المطالبة",
        "core": ["civil-transactions-law", "law-of-evidence", "electronic-transactions-law"],
        "companions": ["nzam-almhakm-altjaryh", "nzam-altkalyf-alqdayyh"],
        "optional": ["execution-law"],
        "excluded": ["nzam-drybh-alqymh-almdafh"],
    },
    {
        "family_id": "civil_private_construction_defects_building_code",
        "domain": "real_estate_construction",
        "scenario": "مالك تعاقد مع مقاول خاص لبناء فيلا، تأخر المقاول ثمانية أشهر وبعد التسليم ظهرت عيوب في العزل والكهرباء والخرسانة",
        "core": ["civil-transactions-law", "nzam-ttbyq-kwd-albnaa-alsawdy"],
        "companions": ["law-of-evidence", "nzam-tsnyf-almqawlyn"],
        "optional": ["nzam-almhakm-altjaryh"],
        "excluded": ["government-tenders-and-procurement-law"],
    },
    {
        "family_id": "civil_hidden_defect_used_car_fraud",
        "domain": "civil_evidence_procedure",
        "scenario": "مشتري سيارة مستعملة اكتشف عيباً خفياً في المحرك أخفاه البائع، ويريد الفسخ أو إنقاص الثمن وإثبات الغش والضرر",
        "core": ["civil-transactions-law"],
        "companions": ["law-of-evidence", "commercial-fraud-law"],
        "optional": ["nzam-almhakm-altjaryh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "arbitration_award_annulment_enforcement",
        "domain": "civil_evidence_procedure",
        "scenario": "صدر حكم تحكيم تجاري، ويريد المحكوم عليه بطلانه بحجة تجاوز هيئة التحكيم لصلاحياتها، بينما يطلب الطرف الآخر الأمر بالتنفيذ",
        "core": ["nzam-althkym"],
        "companions": ["execution-law", "law-of-evidence"],
        "optional": ["nzam-almhakm-altjaryh"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "execution_electronic_promissory_note_enforcement",
        "domain": "civil_evidence_procedure",
        "scenario": "دائن يحمل سنداً لأمر إلكترونياً ورسائل إقرار بالدين، ويطلب التنفيذ، بينما يدفع المدين بتزوير التوقيع وانعدام الصفة",
        "core": ["execution-law", "nzam-alawraq-altjaryh"],
        "companions": ["execution-implementing-regulation", "law-of-evidence", "electronic-transactions-law"],
        "optional": ["nzam-altkalyf-alqdayyh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "offplan_sale_delay_specs_escrow",
        "domain": "real_estate_construction",
        "scenario": "مشتر تعاقد على وحدة بيع على الخارطة، تأخر المطور وغير المواصفات، ويدعي المشتري أن الدفعات لم تودع في حساب الضمان",
        "core": ["nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth"],
        "companions": ["civil-transactions-law", "real-estate-brokerage-law", "law-of-evidence"],
        "optional": ["nzam-altsjyl-alayny-llaqar"],
        "excluded": ["nzam-mlkyh-alwhdat-alaqaryh-wfrzha-wadarha"],
    },
    {
        "family_id": "real_estate_broker_commission_disclosure",
        "domain": "real_estate_construction",
        "scenario": "وسيط عقاري أعلن وحدة بمعلومات ناقصة، تقاضى عمولة من الطرفين دون إفصاح واضح، وثار نزاع عن العربون والتفويض والتسويق",
        "core": ["real-estate-brokerage-law"],
        "companions": ["civil-transactions-law", "law-of-evidence"],
        "optional": ["nzam-altsjyl-alayny-llaqar"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "bankruptcy_preferential_payment_related_supplier_wages",
        "domain": "finance_insolvency",
        "scenario": "شركة توقفت عن السداد، دفعت لمورد قريب من المدير قبل طلب إعادة التنظيم المالي، وتأخرت رواتب الموظفين، ويعترض الدائنون على تفضيل بعضهم",
        "core": ["nzam-aliflas"],
        "companions": ["bankruptcy-implementing-regulation", "labor-law", "companies-law"],
        "optional": ["law-of-evidence"],
        "excluded": ["execution-law"],
    },
    {
        "family_id": "bank_payment_unauthorized_transfer_fintech",
        "domain": "finance_insolvency",
        "scenario": "عميل اكتشف تحويلات غير مصرح بها من محفظة دفع إلكترونية وحساب بنكي، والشركة المرخصة تأخرت في معالجة الشكوى وحفظ السجلات",
        "core": ["nzam-almdfwaat-wkhdmatha", "nzam-albnk-almrkzy-alsawdy"],
        "companions": ["nzam-mraqbh-albnwk", "personal-data-protection-law"],
        "optional": ["anti-cybercrime-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "insurance_denied_health_claim_policy_terms",
        "domain": "finance_insolvency",
        "scenario": "شركة تأمين رفضت مطالبة صحية رغم وجود وثيقة تأمين تعاوني، والنزاع يتعلق بالإفصاح عن الشروط والاستثناءات وحقوق المؤمن له",
        "core": ["nzam-mraqbh-shrkat-altamyn-altaawny", "nzam-aldman-alshy-altaawny"],
        "companions": ["civil-transactions-law", "law-of-evidence"],
        "optional": ["alnzam-alshy"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "movable_security_pledge_priority_enforcement",
        "domain": "finance_insolvency",
        "scenario": "شركة رهنت معدات ومخزوناً ضماناً لقرض، ثم باعت بعض الأصول ونشأ نزاع بين دائن مضمون ودائنين آخرين حول الأولوية والتنفيذ",
        "core": ["nzam-dman-alhqwq-balamwal-almnqwlh"],
        "companions": ["civil-transactions-law", "execution-law", "law-of-evidence"],
        "optional": ["nzam-aliflas"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "copyright_digital_course_copy_platform",
        "domain": "ip_media_telecom",
        "scenario": "منصة نسخت دورة رقمية ومقاطع تعليمية ونشرتها دون إذن المؤلف، مع مطالبة بالتعويض وإزالة المحتوى وإثبات الملكية",
        "core": ["copyright-law"],
        "companions": ["law-of-evidence", "nzam-almhakm-altjaryh"],
        "optional": ["e-commerce-law"],
        "excluded": ["personal-data-protection-law"],
    },
    {
        "family_id": "trademark_counterfeit_import_gcc",
        "domain": "ip_media_telecom",
        "scenario": "تاجر استورد منتجات تحمل علامة مشابهة لعلامة مسجلة في الخليج، ويطلب المالك إجراءات تحفظية وتعويضاً ومنع التداول",
        "core": ["nzam-alalamat-altjaryh"],
        "companions": ["qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh", "law-of-evidence", "commercial-fraud-law"],
        "optional": ["nzam-qanwn-aljmark-almwhd-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "telecom_spam_location_data_marketing",
        "domain": "ip_media_telecom",
        "scenario": "مزود خدمة اتصالات يرسل رسائل تسويقية مستمرة ويستخدم بيانات الموقع وسجل الاستخدام لأغراض إعلانية دون موافقة واضحة",
        "core": ["communications-and-information-technology-law", "personal-data-protection-law"],
        "companions": ["pdpl-implementing-regulation"],
        "optional": ["anti-cybercrime-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "private_health_facility_malpractice_records",
        "domain": "health_food_drugs",
        "scenario": "مريض تضرر من خطأ طبي في منشأة صحية خاصة، ويريد ملفه الطبي وإثبات المسؤولية المهنية والتعويض",
        "core": ["alnzam-alshy", "nzam-almwssat-alshyh-alkhash", "nzam-mzawlh-almhn-alshyh"],
        "companions": ["law-of-evidence", "personal-data-protection-law"],
        "optional": ["civil-transactions-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "medical_device_import_recall_adverse_event",
        "domain": "health_food_drugs",
        "scenario": "مورد أجهزة طبية أدخل جهازاً للسوق دون استيفاء متطلبات الهيئة، وظهرت بلاغات أعطال وسلامة ويطلب المستهلكون السحب والتعويض",
        "core": ["nzam-alajhzh-walmstlzmat-altbyh", "nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
        "companions": ["commercial-fraud-law", "law-of-evidence"],
        "optional": ["e-commerce-law"],
        "excluded": ["companies-law"],
    },
    {
        "family_id": "food_contamination_labeling_recall",
        "domain": "health_food_drugs",
        "scenario": "مصنع غذائي وزع منتجاً ملوثاً مع بطاقة بيانات مضللة، وصدرت بلاغات تسمم ويطلب المستهلكون معرفة أحكام السحب والعقوبات",
        "core": ["nzam-alghdhaa", "nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
        "companions": ["commercial-fraud-law", "law-of-evidence"],
        "optional": ["nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "cosmetics_unsafe_product_claims",
        "domain": "health_food_drugs",
        "scenario": "متجر ومورد مستحضرات تجميل باعا منتجاً سبب حساسية شديدة، وتضمن الإعلان ادعاءات علاجية بلا ترخيص وبيانات ناقصة",
        "core": ["nzam-mntjat-altjmyl", "nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
        "companions": ["commercial-fraud-law", "e-commerce-law"],
        "optional": ["law-of-evidence"],
        "excluded": ["copyright-law"],
    },
    {
        "family_id": "personal_status_divorce_custody_alimony",
        "domain": "family_criminal_protection",
        "scenario": "نزاع أسري حول فسخ نكاح ونفقة وحضانة وزيارة، مع رسائل تثبت الإنفاق والامتناع وطلب تدابير عاجلة لمصلحة طفل",
        "core": ["personal-status-law"],
        "companions": ["law-of-evidence", "nzam-hmayh-altfl"],
        "optional": ["protection-from-abuse-law"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "child_abuse_school_reporting",
        "domain": "family_criminal_protection",
        "scenario": "مدرسة لاحظت آثار إيذاء على طفل ولم تبلغ فوراً، ثم ظهر خلاف حول واجب الحماية والسرية والإجراءات أمام الجهات المختصة",
        "core": ["nzam-hmayh-altfl", "protection-from-abuse-law"],
        "companions": ["criminal-procedure-law", "law-of-evidence"],
        "optional": ["personal-status-law"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "cybercrime_account_hack_extortion_data",
        "domain": "privacy_data",
        "scenario": "شخص اخترق حسابات عملاء واستولى على بياناتهم وهدد بنشرها، والشركة تريد تحديد النصوص المتعلقة بالاختراق والابتزاز والتزامات حماية البيانات",
        "core": ["anti-cybercrime-law", "personal-data-protection-law"],
        "companions": ["pdpl-implementing-regulation", "criminal-procedure-law"],
        "optional": ["law-of-evidence"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "aml_suspicious_transactions_beneficial_owner",
        "domain": "finance_insolvency",
        "scenario": "منشأة مالية لاحظت تحويلات مجزأة وملاكاً مستفيدين غير واضحين، وتأخرت في الإبلاغ عن الاشتباه وحفظ السجلات",
        "core": ["anti-money-laundering-law"],
        "companions": ["nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh", "nzam-mraqbh-albnwk"],
        "optional": ["criminal-procedure-law"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "environment_pollution_license_penalty",
        "domain": "health_food_drugs",
        "scenario": "مصنع صرف مخلفات في وادٍ دون ترخيص بيئي كاف، وظهرت أضرار على سكان ومزارع مجاورة مع مطالبة بإيقاف النشاط والتعويض",
        "core": ["nzam-albyyh"],
        "companions": ["civil-transactions-law", "law-of-evidence"],
        "optional": ["nzam-alanshth-almqlqh-llrahh-aw-alkhtrh-aw-almdrh-balshh-aw-albyyh"],
        "excluded": ["labor-law"],
    },
    {
        "family_id": "municipal_license_hazardous_activity",
        "domain": "procurement_admin",
        "scenario": "منشأة تمارس نشاطاً مقلقاً للراحة أو مضراً بالصحة داخل حي سكني دون ترخيص بلدي واضح، والبلدية أوقفت النشاط وفرضت غرامة",
        "core": ["nzam-ijraaat-altrakhys-albldyh", "nzam-alanshth-almqlqh-llrahh-aw-alkhtrh-aw-almdrh-balshh-aw-albyyh"],
        "companions": ["nzam-almrafaat-amam-dywan-almzalm", "law-of-evidence"],
        "optional": ["nzam-albyyh"],
        "excluded": ["e-commerce-law"],
    },
    {
        "family_id": "commercial_concealment_foreign_operator_register",
        "domain": "corporate_commercial",
        "scenario": "مواطن مكّن غير سعودي من تشغيل متجر باسمه وسجله التجاري وحسابه البنكي مقابل نسبة، مع فواتير وتحويلات تثبت الإدارة الفعلية",
        "core": ["nzam-mkafhh-altstr"],
        "companions": ["nzam-alsjl-altjary", "companies-law", "law-of-evidence"],
        "optional": ["anti-money-laundering-law"],
        "excluded": ["labor-law"],
    },
]


QUESTION_STYLES = (
    "استرجع جميع الأنظمة واللوائح السعودية الواجبة التطبيق على واقعة: {scenario}. صنفها إلى مراجع إلزامية ومساندة ومراجع مستبعدة، واذكر المواد الأقرب.",
    "محام يراجع ملفاً في 2026 وفيه الآتي: {scenario}. ما الحزمة النظامية الكاملة التي يجب أن يجمعها RAG قبل صياغة الجواب؟",
    "اختبار جمع لا فتوى: {scenario}. اجمع النصوص الحاكمة واللوائح التنفيذية والضوابط القطاعية، ولا تكتف باسم النظام العام.",
    "لأغراض تقييم RAG قانوني سعودي، ما المراجع النظامية التي يجب استرجاعها عند وجود الوقائع التالية: {scenario}؟",
    "أعد بناء الحزمة القانونية لهذه الواقعة المركبة: {scenario}. افصل بين النظام الأساسي واللائحة التنفيذية والضوابط القطاعية والمرجع المشروط.",
    "صياغة مختلفة للاختبار: {scenario}. المطلوب جمع المواد والأنظمة السعودية ذات الصلة دون إسقاط اللوائح الخاصة.",
    "لا تجب عن الواقعة، بل اجمع مصادرها النظامية: {scenario}. المطلوب أن تظهر اللوائح المكملة قبل أي تحليل.",
    "في مراجعة أولية لملف نزاع سعودي: {scenario}. ما الأنظمة واللوائح التي يجب أن تكون ضمن حزمة الاسترجاع؟",
    "سؤال مموه لاختبار الجمع: {scenario}. استخرج النظام الحاكم واللوائح التنفيذية والضوابط القطاعية ذات الصلة.",
    "قضية مركبة وردت للباحث القانوني: {scenario}. اذكر الحزمة النظامية الواجبة ولا تسقط المرجع الخاص بسبب وجود نظام عام.",
    "اختبر قدرة RAG على الجمع في هذه الواقعة: {scenario}. المطلوب مراجع أساسية ومساندة ومشروطة ومستبعدة.",
    "يريد المراجع الخارجي معرفة هل يغطي الاسترجاع هذه الواقعة: {scenario}. اجمع كل المصادر النظامية الواجبة التطبيق.",
    "في صياغة عامية قريبة من المستخدم: {scenario}. ما النصوص واللوائح التي يجب إحضارها أولاً؟",
    "في صياغة مهنية مختصرة: {scenario}. حدد الحزمة القانونية السعودية الكاملة قبل الجواب.",
    "استعلام لمؤشر اكتمال الحزمة: {scenario}. ما المصادر التي يعد غيابها نقصاً جوهرياً في RAG؟",
    "استعلام لمؤشر منع الانجراف: {scenario}. اجمع المراجع القريبة، وبيّن ما لا ينبغي أن يحل محل النظام الخاص.",
    "حالة متداخلة بين أكثر من نظام: {scenario}. اجمع الأنظمة واللوائح والضوابط اللازمة لتغطية كل محور.",
    "المطلوب استرجاع لا استشارة: {scenario}. اذكر المراجع النظامية التي يجب أن تظهر في النتائج.",
    "افترض أن المستخدم طلب جمع النصوص فقط لهذه الواقعة: {scenario}. ما الحزمة التي يجب أن يستدعيها النظام؟",
    "افترض أن الواقعة صيغت بعبارات عامة: {scenario}. استخرج المرجع الخاص لا المرجع العام وحده.",
    "يريد المستخدم ملفاً نظامياً كاملاً حول: {scenario}. اجمع النظام الأساسي واللوائح والقرارات ذات العلاقة.",
    "اختبار gold صعب لعائلة وقائع: {scenario}. ما المصادر الإلزامية وما المصادر المساندة؟",
    "اختبار held-out لعائلة قانونية مركبة: {scenario}. ما الأنظمة التي يجب ألا يفوتها الاسترجاع؟",
    "صياغة طويلة نسبياً لقياس الاستيعاب: {scenario}. اجمع المراجع السعودية التي تغطي الحقوق والالتزامات والإجراءات.",
    "صياغة قصيرة لكن متعددة المحاور: {scenario}. ما المراجع النظامية الأقرب؟",
    "في ملف امتثال أو منازعة: {scenario}. اجمع النصوص النظامية واللوائح التي يعتمد عليها المستشار.",
    "في ملف مطالبات أو مخالفات: {scenario}. حدد النظام الحاكم والمرجع التنفيذي والضابط القطاعي.",
    "في واقعة قد تضلل الاسترجاع إلى نظام عام: {scenario}. أعد الحزمة الصحيحة كاملة.",
    "سؤال مصمم لكشف نقص اللوائح: {scenario}. ما اللائحة أو الضابط الذي يجب أن يظهر مع النظام الأساسي؟",
    "سؤال مصمم لكشف نقص النظام الخاص: {scenario}. ما المرجع الخاص الذي لا يكفي عنه النظام العام؟",
    "سؤال مصمم لكشف نقص الاختصاص والإجراءات: {scenario}. اجمع النصوص الموضوعية والإجرائية معاً.",
    "سؤال مصمم لكشف نقص الإثبات: {scenario}. اجمع النظام الموضوعي ونظام الإثبات والمرجع الإجرائي عند الحاجة.",
    "سؤال مصمم لكشف نقص الامتثال القطاعي: {scenario}. اجمع اللوائح الخاصة والضوابط الفنية بجانب النظام العام.",
    "سؤال مصمم لكشف نقص الحماية أو التعويض: {scenario}. اجمع النصوص التي تغطي الحظر والجزاء والحق في المطالبة.",
)


def present_slugs(values: list[str], all_slugs: set[str]) -> list[str]:
    return list(dict.fromkeys(slug for slug in values if slug in all_slugs))


def companions_for_v3(slug: str, all_slugs: set[str]) -> list[str]:
    return present_slugs(V3_COMPANION_BY_SLUG.get(slug, []), all_slugs)[:7]


def convert_existing_case(case: dict[str, Any]) -> dict[str, Any]:
    row = dict(case)
    row["source_note"] = "regression_from_gold_package_recall_1000_v2"
    row["source_case_id"] = case.get("question_id")
    for key in ("question_id", "benchmark_category", "question_type", "split", "expected_regulations", "allowed_regulations"):
        row.pop(key, None)
    return row


def make_scenario_cases(all_slugs: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for family in SCENARIO_FAMILIES:
        core = present_slugs(family["core"], all_slugs)
        companions = present_slugs(family.get("companions", []), all_slugs)
        missing_core = [slug for slug in family["core"] if slug not in all_slugs]
        missing_companions = [slug for slug in family.get("companions", []) if slug not in all_slugs]
        if missing_core:
            skipped.append({"family_id": family["family_id"], "missing_core": missing_core, "missing_companions": missing_companions})
            continue
        optional = present_slugs(family.get("optional", []), all_slugs)
        excluded = present_slugs(family.get("excluded", []), all_slugs)
        for variant, template in enumerate(QUESTION_STYLES, start=1):
            cases.append(
                {
                    "domain": family["domain"],
                    "scenario_family_id": family["family_id"],
                    "scenario_variant": variant,
                    "question": template.format(scenario=family["scenario"]),
                    "required_core_regulations": core,
                    "required_companion_regulations": companions,
                    "optional_regulations": optional,
                    "excluded_regulations": excluded,
                    "expected_articles": [],
                    "gold_answer_summary": (
                        "يجب جمع الحزمة الكاملة لهذه العائلة: "
                        + "، ".join(core + companions)
                        + "."
                    ),
                    "source_note": "handcrafted_scenario_family_v3",
                }
            )
    return cases, skipped


def make_article_case_v3(
    row: dict[str, Any],
    article: dict[str, Any],
    all_slugs: set[str],
    variant: int,
) -> dict[str, Any]:
    case = make_article_case(row, article, all_slugs, variant)
    slug = row["slug"]
    companions = companions_for_v3(slug, all_slugs)
    if companions:
        case["required_companion_regulations"] = companions
        expected = list(dict.fromkeys(case["required_core_regulations"] + companions))
        case["expected_regulations"] = expected
    case["source_note"] = "article_anchored_machine_generated_v3"
    return case


def assign_ids(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for index, case in enumerate(cases, start=1):
        row = dict(case)
        row["question_id"] = f"gpr_v3_{index:04d}"
        row["benchmark_category"] = f"gold_package_recall_v3_{row.get('domain') or 'unknown'}"
        row["question_type"] = f"package_recall_{SPLIT_CYCLE[(index - 1) % len(SPLIT_CYCLE)]}"
        row["split"] = SPLIT_CYCLE[(index - 1) % len(SPLIT_CYCLE)]
        expected = list(dict.fromkeys(row.get("required_core_regulations", []) + row.get("required_companion_regulations", [])))
        row["expected_regulations"] = expected
        row["allowed_regulations"] = list(dict.fromkeys(expected + row.get("optional_regulations", [])))
        row["min_expected_regulation_hits"] = len(expected)
        row["min_expected_article_hits"] = 0
        row["expected_behavior"] = "answer"
        out.append(row)
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    regs = json.loads(REGULATIONS_PATH.read_text(encoding="utf-8"))
    regs = [row for row in regs if row.get("slug")]
    all_slugs = {row["slug"] for row in regs}

    scenario_cases, skipped_families = make_scenario_cases(all_slugs)
    v2_cases = [convert_existing_case(case) for case in load_jsonl(V2_CASES_PATH)]
    cases: list[dict[str, Any]] = []
    cases.extend(scenario_cases)
    cases.extend(v2_cases)

    needed = TARGET_CASES - len(cases)
    article_choices: dict[str, list[dict[str, Any]]] = {}
    for row in regs:
        article_choices[row["slug"]] = choose_articles(load_regulation_articles(row["slug"]), limit=8)

    per_slug_counter: dict[str, int] = defaultdict(int)
    generated = 0
    ordered_regs = sorted(regs, key=lambda item: (item.get("catalog_source") != "official_catalog", item["slug"]))
    while generated < needed:
        for row in ordered_regs:
            slug = row["slug"]
            choices = article_choices.get(slug) or []
            if not choices:
                continue
            index = per_slug_counter[slug] % len(choices)
            cases.append(make_article_case_v3(row, choices[index], all_slugs, per_slug_counter[slug]))
            per_slug_counter[slug] += 1
            generated += 1
            if generated >= needed:
                break

    cases = assign_ids(cases[:TARGET_CASES])
    with CASES_PATH.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")

    core_slugs = Counter(slug for case in cases for slug in case.get("required_core_regulations", []))
    companion_slugs = Counter(slug for case in cases for slug in case.get("required_companion_regulations", []))
    domains = Counter(case.get("domain") for case in cases)
    splits = Counter(case.get("split") for case in cases)
    source_notes = Counter(case.get("source_note") for case in cases)
    family_counts = Counter(case.get("scenario_family_id") for case in cases if case.get("scenario_family_id"))

    manifest = {
        "benchmark_id": "gold_package_recall_5000_v3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target_cases": TARGET_CASES,
        "cases": len(cases),
        "scenario_family_cases": len(scenario_cases),
        "scenario_families": len(family_counts),
        "regression_from_v2_cases": len(v2_cases),
        "article_generated_cases": source_notes.get("article_anchored_machine_generated_v3", 0),
        "regulations_available": len(regs),
        "regulations_covered_as_core": len(core_slugs),
        "regulations_covered_as_companion": len(companion_slugs),
        "official_catalog_regulations_covered": sum(
            1 for row in regs if row.get("catalog_source") == "official_catalog" and row["slug"] in core_slugs
        ),
        "custom_catalog_regulations_covered": sum(
            1 for row in regs if row.get("catalog_source") == "custom_catalog" and row["slug"] in core_slugs
        ),
        "split_counts": dict(splits),
        "domain_counts": dict(domains),
        "source_note_counts": dict(source_notes),
        "scenario_family_counts": dict(family_counts),
        "least_repeated_core_slugs": core_slugs.most_common()[-20:],
        "skipped_families": skipped_families,
        "anti_leakage": {
            "service_payload_fields": ["question", "answer_mode", "retrieval_profile"],
            "gold_labels_used_only_offline": True,
            "do_not_import_into_rag_engine": True,
        },
        "known_limitations": [
            "Scenario families are deliberately strong seeds, but they are still finite and should grow after new manual failures.",
            "Collection scoring does not penalize extra unrelated regulations; contamination/purity scoring is a separate layer.",
            "Article-anchored cases include machine-generated phrasing from official corpus text and may include OCR-heavy wording.",
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    README_PATH.write_text(
        "\n".join(
            [
                "# Gold Package Recall 5000 v3",
                "",
                "معيار ذهبي خارجي لاختبار جمع الحزمة النظامية في RAG القانوني السعودي.",
                "",
                "## لماذا v3؟",
                "",
                "v2 أغلق 1000 حالة، لكنه لم يكن كافياً لعائلات الوقائع المركبة المفاجئة مثل شركة مدرجة + إفصاح جوهري + تداول بناءً على معلومات داخلية + تعارض مصالح. هذا الإصدار يضيف طبقة seed عائلية قوية ثم يحافظ على regression v2 ويكمل التغطية من corpus الرسمي.",
                "",
                "## منع التغشيش",
                "",
                "- لا تُرسل الإجابات الذهبية إلى الخدمة.",
                "- يرسل runner نص السؤال فقط مع `answer_mode=benchmark` و `retrieval_profile=jamia_recall`.",
                "- التصحيح يحصل بعد رجوع الرد من خلال `required_core_regulations` و `required_companion_regulations`.",
                "- المصادر الزائدة تسجل في هذه المرحلة ولا تخصم؛ تنقية التلويث مرحلة لاحقة.",
                "",
                "## التركيب",
                "",
                f"- الحالات: `{len(cases)}`",
                f"- عائلات الوقائع: `{len(family_counts)}`",
                f"- حالات عائلية مركبة: `{len(scenario_cases)}`",
                f"- regression من v2: `{len(v2_cases)}`",
                f"- حالات مولدة من المواد الرسمية: `{source_notes.get('article_anchored_machine_generated_v3', 0)}`",
                f"- الأنظمة/اللوائح المغطاة كـcore: `{len(core_slugs)}` من `{len(regs)}`",
                "",
                "## ملفات",
                "",
                "- `gold_package_recall_5000_v3.jsonl`",
                "- `manifest.json`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Wrote {CASES_PATH}")


if __name__ == "__main__":
    main()
