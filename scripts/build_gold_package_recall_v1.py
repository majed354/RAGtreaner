"""Build a frozen 100-case gold benchmark for Saudi legal RAG package recall.

The generated benchmark is evaluation-only. It must not be imported by the RAG
engine, prompt builder, ingestion pipeline, or routing bundles.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "eval" / "gold_package_recall_v1"
CASES_PATH = OUT_DIR / "gold_package_recall_100_v1.jsonl"
MANIFEST_PATH = OUT_DIR / "manifest.json"
README_PATH = OUT_DIR / "README.md"
REGULATIONS_PATH = ROOT / "data" / "structured" / "regulations.json"

SPLIT_CYCLE = (
    "dev",
    "regression",
    "heldout",
    "regression",
    "dev",
    "heldout",
    "regression",
    "heldout",
    "dev",
    "regression",
)


def _case(
    domain: str,
    question: str,
    core: list[str],
    companions: list[str] | None = None,
    optional: list[str] | None = None,
    excluded: list[str] | None = None,
    summary: str = "",
) -> dict[str, Any]:
    return {
        "domain": domain,
        "question": question,
        "required_core_regulations": core,
        "required_companion_regulations": companions or [],
        "optional_regulations": optional or [],
        "excluded_regulations": excluded or [],
        "gold_answer_summary": summary,
    }


CASES: list[dict[str, Any]] = [
    _case(
        "labor",
        "عامل غير سعودي بعقد محدد المدة أُنهي عقده فوراً، وله أجور متأخرة وبدل إجازات ومكافأة نهاية خدمة، مع ادعاء مخالفات على صاحب العمل. ما الحزمة النظامية الواجبة الجمع؟",
        ["labor-law"],
        ["labor-implementing-regulation", "wage-protection-rules", "labor-contract-documentation-rules", "labor-violations-penalties-table"],
        ["nzam-altamynat-alajtmaayh"],
        ["civil-transactions-law", "nzam-ttbyq-kwd-albnaa-alsawdy", "nzam-tsnyf-almqawlyn"],
        "نظام العمل هو المركز، وتلحق به اللائحة، حماية الأجور، توثيق العقد، وجدول المخالفات.",
    ),
    _case(
        "labor",
        "موظف خاص عمل ساعات إضافية في العطل، ولم تدفع له الشركة أجر العمل الإضافي، ثم حسمت من راتبه وأنهت عقده دون إشعار كاف.",
        ["labor-law"],
        ["labor-implementing-regulation", "labor-violations-penalties-table", "wage-protection-rules"],
        [],
        ["nzam-alkhdmh-almdnyh"],
        "الحزمة عمالية: ساعات العمل، الأجر الإضافي، الحسم، الإشعار، والجزاءات.",
    ),
    _case(
        "labor",
        "موظفة أبلغت عن تحرش في مكان العمل ثم فصلت واحتجزت مستحقاتها. ما الأنظمة واللوائح التي يجب استرجاعها؟",
        ["labor-law", "nzam-mkafhh-jrymh-althrsh"],
        ["labor-implementing-regulation", "labor-violations-penalties-table"],
        ["law-of-evidence", "whistleblowers-witnesses-experts-and-victims-protection-law"],
        ["nzam-alkhdmh-almdnyh"],
        "يجب جمع نظام العمل مع مكافحة التحرش، ثم اللائحة والمخالفات، والإثبات عند النزاع.",
    ),
    _case(
        "labor",
        "منشأة تؤخر الرواتب شهوراً ولا توثق عقود موظفيها في المنصة المعتمدة وتدفع خارج مسار حماية الأجور.",
        ["labor-law"],
        ["labor-implementing-regulation", "wage-protection-rules", "labor-contract-documentation-rules", "labor-violations-penalties-table"],
        [],
        ["civil-transactions-law"],
        "المركز عمالي تنظيمي: الأجر، حماية الأجور، توثيق العقود، وجدول المخالفات.",
    ),
    _case(
        "labor",
        "عامل تعرض لإصابة عمل داخل مصنع، وتأخر صاحب العمل في الإبلاغ وصرف الحقوق التأمينية، مع مخالفة شروط السلامة المهنية.",
        ["labor-law", "nzam-altamynat-alajtmaayh"],
        ["labor-implementing-regulation", "labor-violations-penalties-table"],
        ["law-of-evidence"],
        ["nzam-alkhdmh-almdnyh"],
        "تجتمع أحكام العمل والسلامة مع التأمينات الاجتماعية لإصابات العمل.",
    ),
    _case(
        "labor",
        "عامل في مقاول من الباطن بمشروع خاص لم يتسلم أجره، وتدعي المنشأة أن المسؤول هو المقاول الرئيس لا صاحب العمل المسجل.",
        ["labor-law"],
        ["labor-implementing-regulation", "wage-protection-rules", "labor-contract-documentation-rules"],
        ["civil-transactions-law", "law-of-evidence"],
        ["government-tenders-and-procurement-law"],
        "لا ينجرف إلى المشتريات الحكومية ما لم تكن الجهة حكومية؛ الأصل علاقة عمل وأجر.",
    ),
    _case(
        "labor",
        "موظف سعودي فصل بسبب غياب يدعي أنه مشروع طبياً، ويطلب أجوره ومكافأة نهاية الخدمة والتعويض عن الفصل.",
        ["labor-law"],
        ["labor-implementing-regulation", "labor-violations-penalties-table"],
        ["law-of-evidence", "nzam-altamynat-alajtmaayh"],
        ["nzam-alkhdmh-almdnyh"],
        "يلزم جمع مواد انتهاء العقد، الجزاءات، الغياب، المستحقات، والإثبات عند النزاع.",
    ),
    _case(
        "labor",
        "موظف حكومي صدر بحقه جزاء تأديبي ثم قرار كف يد، ويريد الطعن في القرار الإداري أمام القضاء المختص.",
        ["nzam-alkhdmh-almdnyh", "nzam-alandbat-alwzyfy"],
        ["nzam-almrafaat-amam-dywan-almzalm"],
        ["law-of-evidence"],
        ["labor-law"],
        "هذه ليست علاقة عمل خاصة؛ المركز الخدمة المدنية والانضباط والطعن الإداري.",
    ),
    _case(
        "ecommerce_consumer",
        "متجر إلكتروني باع جهازاً طبياً منزلياً بادعاءات مضللة، رفض الاسترجاع، وطلب رقم الهوية وتاريخ الميلاد بلا حاجة ظاهرة.",
        ["e-commerce-law"],
        ["ecommerce-implementing-regulation", "commercial-fraud-law", "personal-data-protection-law", "pdpl-implementing-regulation"],
        ["nzam-alajhzh-walmstlzmat-altbyh", "product-safety-law"],
        ["companies-law"],
        "التجارة الإلكترونية والغش وحماية البيانات هي الحزمة الأساسية، والجهاز الطبي طبقة قطاعية محتملة.",
    ),
    _case(
        "ecommerce_consumer",
        "مستهلك دفع لاشتراك رقمي ولم تفعل الخدمة، ورفضت المنصة الإلغاء مع إرسال رسائل تسويقية ومشاركة رقم الجوال مع شريك.",
        ["e-commerce-law"],
        ["ecommerce-implementing-regulation", "personal-data-protection-law", "pdpl-implementing-regulation"],
        ["pdpl-transfer-regulation"],
        ["copyright-law"],
        "التجارة الإلكترونية تقود جانب الخدمة المدفوعة، والبيانات الشخصية طبقة مرافقة لا بديل.",
    ),
    _case(
        "ecommerce_consumer",
        "متجر سعودي أعلن ضمان مدى الحياة لجوال دون شروط، تأخر أكثر من خمسة عشر يوماً، ثم رفض إلغاء الطلب واسترداد المبلغ.",
        ["e-commerce-law"],
        ["ecommerce-implementing-regulation", "commercial-fraud-law"],
        ["zatca-vat-implementing-regulation"],
        ["personal-data-protection-law"],
        "يجب جمع التجارة الإلكترونية ولائحتها والغش التجاري للإعلان المضلل والتأخير.",
    ),
    _case(
        "ecommerce_consumer",
        "سوق إلكتروني يعرض بائعين مجهولين ولا يوضح بيانات موفر الخدمة ولا يصدر فاتورة ضريبية واضحة للمستهلك.",
        ["e-commerce-law"],
        ["ecommerce-implementing-regulation", "nzam-drybh-alqymh-almdafh", "zatca-vat-implementing-regulation"],
        ["zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls"],
        ["companies-law"],
        "الإفصاح الإلكتروني مع طبقة VAT والفوترة عند وجود فاتورة ضريبية.",
    ),
    _case(
        "ecommerce_consumer",
        "حساب إلكتروني يبيع مستحضرات تجميل مقلدة ويدعي موافقات غير صحيحة ويرفض إعادة المبلغ.",
        ["e-commerce-law", "commercial-fraud-law"],
        ["ecommerce-implementing-regulation", "nzam-mntjat-altjmyl", "nzam-alhyyh-alaamh-llghdhaa-waldwaa"],
        ["product-safety-law", "nzam-alalamat-altjaryh"],
        ["nzam-almnafsh"],
        "حزمة متجر إلكتروني وغش، مع نظام منتجات التجميل والهيئة عند المستحضر.",
    ),
    _case(
        "ecommerce_consumer",
        "تطبيق توصيل يجمع موقع العميل وسجل طلباته ويبيعها لشريك إعلاني دون موافقة واضحة.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation", "e-commerce-law", "ecommerce-implementing-regulation"],
        ["pdpl-transfer-regulation"],
        ["commercial-fraud-law"],
        "مركز الواقعة بيانات شخصية وتسويق، مع التجارة الإلكترونية كطبقة خدمة.",
    ),
    _case(
        "ecommerce_consumer",
        "منصة باعت جهازاً منزلياً خطراً ولم تعلن عن الاستدعاء أو مخاطر السلامة للمستهلكين.",
        ["product-safety-law", "e-commerce-law"],
        ["ecommerce-implementing-regulation", "commercial-fraud-law"],
        ["nzam-almwasfat-waljwdh"],
        ["nzam-almnafsh"],
        "سلامة المنتج والتجارة الإلكترونية، مع الغش عند الإخفاء أو الادعاء المضلل.",
    ),
    _case(
        "privacy_data",
        "تطبيق صحي يجمع بيانات هوية وموقع وبيانات صحية، يستضيف قاعدة البيانات خارج المملكة، وحدث تسرب أبلغ عنه بعد أسبوعين.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation", "pdpl-transfer-regulation", "anti-cybercrime-law"],
        ["alnzam-alshy", "nzam-almwssat-alshyh-alkhash"],
        ["e-commerce-law"],
        "PDPL ولائحته ولائحة النقل هي المركز، والجرائم المعلوماتية للجانب الجنائي.",
    ),
    _case(
        "privacy_data",
        "مدرسة خاصة تجمع بصمات وبيانات صحية للطلاب وتشاركها مع تطبيق خارجي دون سياسة خصوصية واضحة.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation"],
        ["pdpl-transfer-regulation", "nzam-hmayh-altfl"],
        ["e-commerce-law"],
        "حماية البيانات تقود، والطفل طبقة حماية محتملة عند بيانات القصر.",
    ),
    _case(
        "privacy_data",
        "صاحب عمل يرسل بيانات موظفيه إلى معالج رواتب أجنبي خارج المملكة دون بيان مسوغ النقل أو اتفاق المعالجة.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation", "pdpl-transfer-regulation", "labor-law"],
        ["wage-protection-rules"],
        ["e-commerce-law"],
        "بيانات الموظفين ونقلها للخارج، مع العمل وحماية الأجور كطبقة سياقية.",
    ),
    _case(
        "privacy_data",
        "بنك يستخدم أرقام جوالات العملاء في رسائل تسويق مباشر ويشارك شرائح العملاء مع مزود تحليلات.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation"],
        ["nzam-mraqbh-albnwk", "nzam-albnk-almrkzy-alsawdy"],
        ["e-commerce-law"],
        "التسويق المباشر ومشاركة البيانات تحت PDPL، مع طبقة مصرفية عند البنك.",
    ),
    _case(
        "privacy_data",
        "مركز تجاري يستخدم كاميرات تعرف على الوجه لتحليل حركة الزوار دون لوحات إفصاح أو موافقة.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation"],
        ["nzam-astkhdam-kamyrat-almraqbh-alamnyh"],
        ["anti-cybercrime-law"],
        "بيانات شخصية/حيوية مع كاميرات المراقبة عند وجود نظام كاميرات.",
    ),
    _case(
        "privacy_data",
        "عميل طلب نسخة من بياناته وتصحيحها وحذفها من منصة، لكن جهة التحكم تجاهلت الطلبات.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation"],
        [],
        ["e-commerce-law"],
        "حقوق صاحب البيانات وطلبات الوصول والتصحيح والحذف تحت PDPL ولائحته.",
    ),
    _case(
        "privacy_data",
        "معالج بيانات تعرض لتسرب ولم يبلغ جهة التحكم فوراً، وجهة التحكم لم تعين مسؤول حماية بيانات رغم معالجة حساسة واسعة.",
        ["personal-data-protection-law"],
        ["pdpl-implementing-regulation"],
        ["anti-cybercrime-law"],
        ["e-commerce-law"],
        "واجبات جهة التحكم والمعالج، التسرب، ومسؤول حماية البيانات داخل اللائحة.",
    ),
    _case(
        "privacy_data",
        "مهاجم اخترق منصة وسرق قاعدة العملاء وطلب فدية، والمنصة قصرت في إشعار أصحاب البيانات والجهة المختصة.",
        ["personal-data-protection-law", "anti-cybercrime-law"],
        ["pdpl-implementing-regulation"],
        ["pdpl-transfer-regulation"],
        ["e-commerce-law"],
        "يجب فصل التزام جهة التحكم عن مسؤولية المخترق الجنائية.",
    ),
    _case(
        "tax_zatca",
        "منشأة خاضعة للضريبة تصدر فواتير PDF عادية لا تتضمن حقول الفاتورة الإلكترونية وتطبق VAT بنسبة خاطئة.",
        ["nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls"],
        [],
        ["electronic-transactions-law"],
        "الفوترة الإلكترونية/VAT لا تعاملات إلكترونية عامة.",
    ),
    _case(
        "tax_zatca",
        "مطعم يصدر فواتير مبسطة بلا رمز QR ولا رقم ضريبي لبعض المبيعات النقدية.",
        ["nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls"],
        [],
        ["e-commerce-law"],
        "الحزمة الضريبية والفنية للفوترة المبسطة.",
    ),
    _case(
        "tax_zatca",
        "شركة أصدرت إشعارات خصم يدوية بعد إرجاع مبالغ للعملاء ولم تربطها بالفواتير الأصلية.",
        ["nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls"],
        [],
        ["electronic-transactions-law"],
        "إشعارات الخصم والربط بالفاتورة ضمن VAT والفوترة الإلكترونية.",
    ),
    _case(
        "tax_zatca",
        "مزود سعودي يصدر خدمات استشارية لعميل خارج المملكة ويريد تحديد المعاملة الضريبية ومكان التوريد.",
        ["nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation"],
        [],
        ["zatca-e-invoicing-technical-controls"],
        "مكان التوريد والتصدير في VAT ولائحته؛ الضوابط الفنية ليست مركزية إلا عند الفاتورة.",
    ),
    _case(
        "tax_zatca",
        "بيع عقار تجاري بين منشأتين، ويثار النزاع حول ضريبة التصرفات العقارية وضريبة القيمة المضافة.",
        ["nzam-drybh-altsrfat-alaqaryh", "nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation"],
        ["civil-transactions-law"],
        ["e-commerce-law"],
        "يلزم جمع ضريبة التصرفات العقارية وVAT عند تكييف التوريد العقاري.",
    ),
    _case(
        "tax_zatca",
        "مستورد أدخل أجهزة عبر المنفذ الجمركي ولم يصرح بالقيمة الصحيحة ويجادل في الرسوم والضريبة عند الاستيراد.",
        ["nzam-qanwn-aljmark-almwhd-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh", "nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation"],
        [],
        ["e-commerce-law"],
        "الجمارك وVAT على الاستيراد هما مركز القضية.",
    ),
    _case(
        "tax_zatca",
        "منصة وسيطة تتقاضى عمولات من البائعين وتصدر فواتير إلكترونية لهم، مع جدل حول الضريبة على العمولة.",
        ["nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation", "zatca-e-invoicing-bylaw", "zatca-e-invoicing-technical-controls"],
        ["e-commerce-law", "ecommerce-implementing-regulation"],
        ["companies-law"],
        "VAT والفوترة للعمولة، والتجارة الإلكترونية سياق منصة فقط.",
    ),
    _case(
        "tax_zatca",
        "منشأة لديها ربط زكوي وضريبي وتريد الاعتراض على ربط الزكاة مع مطالبات VAT منفصلة.",
        ["nzam-jbayh-alzkah", "nzam-drybh-alqymh-almdafh"],
        ["zatca-vat-implementing-regulation"],
        [],
        ["nzam-drybh-aldkhl"],
        "يجب عدم خلط الزكاة وVAT والدخل إلا إذا ظهرت وقائع كل واحد.",
    ),
    _case(
        "corporate_commercial",
        "مدير شركة ذات مسؤولية محدودة باع أصلاً جوهرياً دون موافقة، منع شريكاً من الاطلاع، ووزع أرباحاً صورية.",
        ["companies-law"],
        ["companies-implementing-regulation"],
        ["civil-transactions-law", "law-of-evidence", "nzam-almhakm-altjaryh"],
        ["nzam-aliflas"],
        "الشركات أولاً، ثم المسؤولية المدنية والإثبات عند النزاع.",
    ),
    _case(
        "corporate_commercial",
        "شركة مساهمة مدرجة تريد زيادة رأس المال بمنح أسهم عبر جمعية غير عادية، مع متطلبات إفصاح للمساهمين.",
        ["companies-law", "nzam-alswq-almalyh"],
        ["companies-implementing-regulation", "cma-continuing-obligations-rules", "cma-corporate-governance-regulations", "cma-securities-offering-rules"],
        [],
        ["nzam-aliflas"],
        "الشركات والسوق المالية ولوائح CMA للشركات المدرجة وزيادة رأس المال.",
    ),
    _case(
        "corporate_commercial",
        "شركة مهيمنة تستحوذ على منافس ناشئ، والصفقة قد تؤثر في المنافسة داخل المملكة.",
        ["nzam-almnafsh"],
        ["companies-law"],
        ["nzam-alswq-almalyh"],
        ["e-commerce-law"],
        "التركز الاقتصادي والمنافسة هو المركز، والشركات للشكل فقط.",
    ),
    _case(
        "corporate_commercial",
        "منصة توصيل ذات حصة كبيرة تلزم المطاعم بحصرية وتمنعها من التعامل مع منصات أخرى.",
        ["nzam-almnafsh"],
        [],
        ["civil-transactions-law"],
        ["e-commerce-law"],
        "إساءة الوضع المهيمن والحصرية تحت نظام المنافسة لا التجارة الإلكترونية وحدها.",
    ),
    _case(
        "corporate_commercial",
        "امتياز تجاري لتشغيل علامة مطاعم، مع نزاع حول وثيقة الإفصاح والقيد والسجل التجاري والاسم التجاري.",
        ["nzam-alamtyaz-altjary"],
        ["nzam-alsjl-altjary", "nzam-alasmaa-altjaryh", "nzam-alalamat-altjaryh"],
        ["companies-law", "civil-transactions-law"],
        ["nzam-almnafsh"],
        "نظام الامتياز هو المركز، ثم السجل والأسماء والعلامات.",
    ),
    _case(
        "corporate_commercial",
        "وكيل تجاري حصري يدعي إنهاء غير مشروع لعقد الوكالة ورفض تسجيل أو شطب الوكالة.",
        ["nzam-alwkalat-altjaryh"],
        ["civil-transactions-law", "nzam-almhakm-altjaryh"],
        ["law-of-evidence"],
        ["labor-law"],
        "الحزمة هي الوكالات التجارية مع العقد والإثبات.",
    ),
    _case(
        "corporate_commercial",
        "تاجر يستخدم اسماً تجارياً مشابهاً لمنافسه ولم يحدث بيانات السجل التجاري بعد تغيير النشاط.",
        ["nzam-alasmaa-altjaryh", "nzam-alsjl-altjary"],
        ["nzam-alalamat-altjaryh"],
        ["commercial-fraud-law"],
        ["companies-law"],
        "الأسماء التجارية والسجل هما المركز، والعلامات عند وجود علامة.",
    ),
    _case(
        "corporate_commercial",
        "متجر يبيع منتجات تحمل علامة تجارية مقلدة ويستعمل شعاراً لا يملكه في الإعلان.",
        ["nzam-alalamat-altjaryh", "commercial-fraud-law"],
        ["e-commerce-law", "ecommerce-implementing-regulation"],
        ["qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"],
        ["companies-law"],
        "العلامات والغش، ومع التجارة الإلكترونية عند البيع الإلكتروني.",
    ),
    _case(
        "corporate_commercial",
        "وافد يدير متجراً باسم مواطن ويتحكم بالإيرادات والحسابات والعقود دون ترخيص استثمار.",
        ["nzam-mkafhh-altstr"],
        ["nzam-alsjl-altjary", "companies-law"],
        ["anti-money-laundering-law"],
        ["labor-law"],
        "مركز الواقعة مكافحة التستر، لا مجرد شركات أو عمل.",
    ),
    _case(
        "corporate_commercial",
        "شركة متعثرة دفعت لمورد قريب من المدير قبل طلب إعادة التنظيم المالي وتأخرت في أجور الموظفين.",
        ["nzam-aliflas"],
        ["bankruptcy-implementing-regulation", "labor-law"],
        ["companies-law"],
        ["execution-law"],
        "الإفلاس ولائحته مع أجور العمال كطبقة حقوق دائنين.",
    ),
    _case(
        "corporate_commercial",
        "شركة مدرجة أبرمت صفقة مع طرف ذي علاقة ولم تفصح عنها في الوقت النظامي.",
        ["nzam-alswq-almalyh", "companies-law"],
        ["cma-continuing-obligations-rules", "cma-corporate-governance-regulations", "companies-implementing-regulation"],
        [],
        ["nzam-almnafsh"],
        "الشركات المدرجة تحتاج نظام السوق ولوائح الإفصاح والحوكمة.",
    ),
    _case(
        "corporate_commercial",
        "شخص يسوق أوراقاً مالية ونصائح استثمارية للجمهور دون ترخيص ويجمع مبالغ للاكتتاب.",
        ["nzam-alswq-almalyh"],
        ["cma-securities-offering-rules", "cma-continuing-obligations-rules"],
        ["anti-money-laundering-law"],
        ["e-commerce-law"],
        "السوق المالية والطرح غير المرخص، لا التجارة الإلكترونية لمجرد التسويق الرقمي.",
    ),
    _case(
        "civil_evidence_procedure",
        "مطالبة تجارية بين منشأتين مبنية على عرض سعر ورسائل واتساب وبريد إلكتروني وفواتير، والمشتري ينكر الالتزام.",
        ["civil-transactions-law", "law-of-evidence", "electronic-transactions-law"],
        ["nzam-almhakm-altjaryh", "nzam-altkalyf-alqdayyh"],
        ["zatca-e-invoicing-bylaw", "zatca-vat-implementing-regulation"],
        ["execution-law", "companies-law"],
        "مطالبة وإثبات تجاري قبل التنفيذ: العقد المدني، الإثبات، التعاملات الإلكترونية، والمحاكم التجارية.",
    ),
    _case(
        "civil_evidence_procedure",
        "سند لأمر إلكتروني موقّع رقمياً حل أجله، ويريد الدائن تقديمه للتنفيذ مع إنكار المدين للتوقيع.",
        ["execution-law", "electronic-transactions-law", "law-of-evidence"],
        ["execution-implementing-regulation"],
        ["nzam-alawraq-altjaryh"],
        ["nzam-almhakm-altjaryh"],
        "عند طلب التنفيذ تظهر حزمة التنفيذ مع السند الإلكتروني والإثبات.",
    ),
    _case(
        "civil_evidence_procedure",
        "عقد توريد تجاري يتضمن شرط تحكيم، صدر حكم تحكيم ويريد الطرف الآخر بطلانه أو تنفيذه.",
        ["nzam-althkym"],
        ["law-of-evidence", "execution-law", "execution-implementing-regulation"],
        ["nzam-almhakm-altjaryh"],
        ["labor-law"],
        "التحكيم هو المركز، والتنفيذ عند تنفيذ الحكم، والإثبات عند المنازعة.",
    ),
    _case(
        "civil_evidence_procedure",
        "مالك تعاقد مع مقاول لبناء فيلا خاصة، تأخر المقاول وظهرت عيوب في الخرسانة والكهرباء والعزل.",
        ["civil-transactions-law", "nzam-ttbyq-kwd-albnaa-alsawdy"],
        ["law-of-evidence"],
        ["nzam-tsnyf-almqawlyn", "nzam-alhyyh-alsawdyh-llmhndsyn"],
        ["government-tenders-and-procurement-law"],
        "عقد مقاولة خاص: المعاملات المدنية وكود البناء، لا المشتريات الحكومية.",
    ),
    _case(
        "civil_evidence_procedure",
        "مشتري سيارة مستعملة اكتشف عيباً خفياً أخفاه البائع ويريد الفسخ أو إنقاص الثمن والتعويض.",
        ["civil-transactions-law"],
        ["law-of-evidence"],
        ["commercial-fraud-law"],
        ["e-commerce-law"],
        "البيع والعيب الخفي مدني، والغش التجاري إن كان بائعاً تجارياً أو بيانات مضللة.",
    ),
    _case(
        "civil_evidence_procedure",
        "شركة استأجرت معدات بعقد تمويل إيجاري وتعثرت في السداد، والمؤجر يطلب استرداد الأصل والتعويض.",
        ["nzam-alayjar-altmwyly"],
        ["civil-transactions-law", "law-of-evidence"],
        ["execution-law"],
        ["labor-law"],
        "الإيجار التمويلي هو النص الخاص، مع المدني والإثبات.",
    ),
    _case(
        "civil_evidence_procedure",
        "دائن لديه ضمان على منقولات الشركة ويريد ترتيب حقه عند تعثر المدين ومزاحمة دائنين آخرين.",
        ["nzam-dman-alhqwq-balamwal-almnqwlh"],
        ["civil-transactions-law"],
        ["nzam-aliflas", "execution-law"],
        ["nzam-altmwyl-alaqary"],
        "ضمان الحقوق بالأموال المنقولة هو المركز، ثم الإفلاس أو التنفيذ بحسب المسار.",
    ),
    _case(
        "civil_evidence_procedure",
        "دعوى تجارية أمام المحكمة التجارية بشأن توريد بضائع، مع دفع بعدم الاختصاص ومطالبة بالتكاليف القضائية.",
        ["nzam-almhakm-altjaryh", "nzam-altkalyf-alqdayyh"],
        ["law-of-evidence", "civil-transactions-law"],
        [],
        ["execution-law"],
        "المحاكم التجارية والتكاليف والإثبات، لا التنفيذ إلا بعد حكم أو سند.",
    ),
    _case(
        "civil_evidence_procedure",
        "نزاع حول كمبيالة وسند تجاري بين شركتين، مع ادعاء تزوير التوقيع وفوات الميعاد.",
        ["nzam-alawraq-altjaryh"],
        ["law-of-evidence", "nzam-almhakm-altjaryh"],
        ["electronic-transactions-law"],
        ["e-commerce-law"],
        "الأوراق التجارية هي النظام الخاص، والإثبات للتزوير.",
    ),
    _case(
        "civil_evidence_procedure",
        "متضرر يطالب بتعويض عن فعل ضار تسبب بخسائر مالية، ويحتاج إثبات الضرر وعلاقة السببية.",
        ["civil-transactions-law"],
        ["law-of-evidence"],
        ["law-of-sharia-procedure"],
        ["nzam-almhakm-altjaryh"],
        "المسؤولية والتعويض في المعاملات المدنية، والإثبات لإثبات الضرر.",
    ),
    _case(
        "civil_evidence_procedure",
        "طرفان وقعا عقداً عبر منصة بتوقيع إلكتروني، ثم أنكر أحدهما حجية السجل والتوقيع.",
        ["electronic-transactions-law", "law-of-evidence"],
        ["civil-transactions-law"],
        [],
        ["zatca-e-invoicing-bylaw"],
        "التعاملات الإلكترونية والإثبات هما المركز، لا الفوترة الإلكترونية الضريبية.",
    ),
    _case(
        "procurement_admin",
        "منافسة حكومية ظهر فيها قريب لأحد الموردين ضمن لجنة الفحص، واتفق موردان على رفع الأسعار وتقسيم العطاءات.",
        ["government-tenders-and-procurement-law", "nzam-almnafsh"],
        ["government-procurement-implementing-regulation", "procurement-conflict-of-interest-regulation", "procurement-conduct-ethics-regulation"],
        ["nzam-almrafaat-amam-dywan-almzalm"],
        ["nzam-mkafhh-alrshwh"],
        "المنافسات واللائحة وتعارض المصالح وسلوكيات القائمين، ومع التواطؤ نظام المنافسة.",
    ),
    _case(
        "procurement_admin",
        "متنافس حكومي اعترض على الترسية وتقييم العروض ويريد معرفة مسار التظلم بعد رفض الجهة.",
        ["government-tenders-and-procurement-law"],
        ["government-procurement-implementing-regulation", "nzam-almrafaat-amam-dywan-almzalm"],
        ["procurement-conduct-ethics-regulation"],
        ["nzam-almnafsh"],
        "التظلم والترسية في نظام المنافسات ولائحته، ثم ديوان المظالم عند الطعن.",
    ),
    _case(
        "procurement_admin",
        "مقاول حكومي تأخر لأن الجهة لم تسلم الموقع في الموعد ويطلب تمديداً وتعويضاً.",
        ["government-tenders-and-procurement-law"],
        ["government-procurement-implementing-regulation"],
        ["nzam-almrafaat-amam-dywan-almzalm"],
        ["civil-transactions-law"],
        "التأخير بسبب الجهة في العقد الحكومي تحت نظام المنافسات ولائحته.",
    ),
    _case(
        "procurement_admin",
        "متعاقد حكومي أسند جزءاً من الأعمال لمقاول باطن دون موافقة مكتوبة، والجهة تهدد بإنهاء العقد.",
        ["government-tenders-and-procurement-law"],
        ["government-procurement-implementing-regulation"],
        ["procurement-conduct-ethics-regulation"],
        ["nzam-tsnyf-almqawlyn"],
        "التعاقد من الباطن في العقود الحكومية تحت النظام واللائحة.",
    ),
    _case(
        "procurement_admin",
        "جهة حكومية لجأت للشراء المباشر بحجة حالة طارئة وتريد معرفة حدود ذلك وضوابط التوثيق.",
        ["government-tenders-and-procurement-law"],
        ["government-procurement-implementing-regulation"],
        ["procurement-conduct-ethics-regulation"],
        ["nzam-mkafhh-alrshwh"],
        "أساليب التعاقد والشراء المباشر في النظام واللائحة.",
    ),
    _case(
        "procurement_admin",
        "مورد استبعد من منافسات حكومية بسبب مخالفات سابقة ويريد الطعن في قرار الاستبعاد أو الحرمان.",
        ["government-tenders-and-procurement-law"],
        ["government-procurement-implementing-regulation", "nzam-almrafaat-amam-dywan-almzalm"],
        ["procurement-conduct-ethics-regulation"],
        ["nzam-almnafsh"],
        "الاستبعاد/الحرمان والتظلم في نظام المنافسات ثم ديوان المظالم.",
    ),
    _case(
        "procurement_admin",
        "موظف في جهة إدارية أصدر قراراً إدارياً بسحب ترخيص، ويريد المتضرر إلغاء القرار والتعويض.",
        ["nzam-almrafaat-amam-dywan-almzalm"],
        ["law-of-evidence"],
        ["civil-transactions-law"],
        ["government-tenders-and-procurement-law"],
        "المركز قضاء إداري أمام ديوان المظالم، وليس مشتريات حكومية.",
    ),
    _case(
        "procurement_admin",
        "صدر حكم إداري نهائي ضد جهة حكومية ولم تنفذه، ويريد المحكوم له إجراءات التنفيذ أمام ديوان المظالم.",
        ["nzam-altnfydh-amam-dywan-almzalm"],
        ["nzam-almrafaat-amam-dywan-almzalm"],
        [],
        ["execution-law"],
        "تنفيذ الأحكام الإدارية أمام ديوان المظالم لا نظام التنفيذ العام وحده.",
    ),
    _case(
        "real_estate_construction",
        "مشترٍ في مشروع بيع على الخارطة يشتكي تأخر المطور وتغيير المواصفات وعدم إيداع الدفعات في حساب الضمان.",
        ["nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth"],
        ["civil-transactions-law"],
        ["real-estate-brokerage-law", "law-of-evidence"],
        ["nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha"],
        "البيع على الخارطة هو النص الخاص، والمدني للتعويض والفسخ.",
    ),
    _case(
        "real_estate_construction",
        "وسيط عقاري أخفى تعارض مصالح وأخذ عمولة من الطرفين دون إفصاح في صفقة بيع عقار.",
        ["real-estate-brokerage-law"],
        ["civil-transactions-law", "law-of-evidence"],
        ["nzam-altsjyl-alayny-llaqar"],
        ["nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth"],
        "الوساطة العقارية هي المركز، مع المدني والإثبات.",
    ),
    _case(
        "real_estate_construction",
        "نزاع على ملكية عقار مسجل عينياً بعد بيعين متعارضين ومطالبة بتصحيح السجل.",
        ["nzam-altsjyl-alayny-llaqar"],
        ["civil-transactions-law", "law-of-evidence"],
        [],
        ["real-estate-brokerage-law"],
        "السجل العيني للعقار عند التسجيل والتصحيح والملكية المسجلة.",
    ),
    _case(
        "real_estate_construction",
        "مالك وحدة في مجمع سكني يطعن في قرارات جمعية الملاك ورسوم الأجزاء المشتركة.",
        ["nzam-mlkyh-alwhdat-alaqaryh-wfrzha-widartha"],
        ["civil-transactions-law"],
        ["law-of-evidence"],
        ["nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth"],
        "ملكية الوحدات وإدارتها للأجزاء المشتركة وجمعية الملاك.",
    ),
    _case(
        "real_estate_construction",
        "ممول عقاري يطالب بتنفيذ رهن عقاري مسجل بعد تعثر العميل في السداد.",
        ["nzam-altmwyl-alaqary", "nzam-alrhn-alaqary-almsjl"],
        ["execution-law", "execution-implementing-regulation"],
        ["civil-transactions-law"],
        ["nzam-bya-wtajyr-mshrwaat-aqaryh-ala-alkharth"],
        "التمويل والرهن العقاري المسجل، ثم التنفيذ عند التنفيذ على الضمان.",
    ),
    _case(
        "real_estate_construction",
        "مستثمر أجنبي يريد تملك عقار داخل المملكة وتأسيس نشاط عليه، مع قيود نظامية محتملة.",
        ["nzam-tmlk-ghyr-alsawdyyn-llaqar"],
        ["nzam-alastthmar"],
        ["nzam-alsjl-altjary"],
        ["real-estate-brokerage-law"],
        "تملك غير السعوديين والاستثمار، لا الوساطة إلا عند وجود وسيط.",
    ),
    _case(
        "real_estate_construction",
        "مالك بنى دون رخصة بلدية وخالف اشتراطات كود البناء، وظهرت عيوب سلامة إنشائية.",
        ["nzam-ttbyq-kwd-albnaa-alsawdy"],
        ["nzam-ijraaat-altrakhys-albldyh"],
        ["civil-transactions-law", "law-of-evidence"],
        ["government-tenders-and-procurement-law"],
        "كود البناء والترخيص البلدي، مع المدني عند المسؤولية.",
    ),
    _case(
        "real_estate_construction",
        "مساهمة عقارية جمعت أموالاً من مستثمرين دون وضوح في الترخيص والإفصاح وإدارة المشروع.",
        ["nzam-almsahmat-alaqaryh"],
        ["nzam-altsjyl-alayny-llaqar"],
        ["civil-transactions-law", "law-of-evidence"],
        ["nzam-alswq-almalyh"],
        "المساهمات العقارية هي المركز، والسوق المالية فقط إذا كانت أوراقاً مالية.",
    ),
    _case(
        "real_estate_construction",
        "نزاع حول ضريبة التصرفات العقارية في إفراغ عقار تجاري ومطالبة باسترداد مبلغ سدد بالخطأ.",
        ["nzam-drybh-altsrfat-alaqaryh"],
        ["civil-transactions-law"],
        ["nzam-altsjyl-alayny-llaqar"],
        ["nzam-drybh-alqymh-almdafh"],
        "ضريبة التصرفات العقارية هي المركز، وVAT مشروط بنوع التوريد.",
    ),
    _case(
        "finance_insolvency",
        "عميل بنك اكتشف تحويلات غير مصرح بها من حسابه، والبنك يشتبه في احتيال وغسل أموال.",
        ["nzam-mraqbh-albnwk", "anti-money-laundering-law", "nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh"],
        ["nzam-albnk-almrkzy-alsawdy", "anti-cybercrime-law"],
        ["personal-data-protection-law"],
        ["e-commerce-law"],
        "بنوك واحتيال وغسل أموال، مع جرائم معلوماتية إذا كان الاختراق إلكترونياً.",
    ),
    _case(
        "finance_insolvency",
        "محفظة مدفوعات إلكترونية عطلت أموال العملاء أياماً ورفضت تنفيذ أوامر الدفع.",
        ["nzam-almdfwaat-wkhdmatha"],
        ["nzam-albnk-almrkzy-alsawdy"],
        ["personal-data-protection-law"],
        ["e-commerce-law"],
        "المدفوعات وخدماتها والنظام المصرفي، لا التجارة الإلكترونية وحدها.",
    ),
    _case(
        "finance_insolvency",
        "شركة تمويل فرضت رسوماً غير مفصح عنها في عقد تمويل استهلاكي ورفضت تزويد العميل بجدول السداد.",
        ["nzam-mraqbh-shrkat-altmwyl"],
        ["civil-transactions-law", "law-of-evidence"],
        ["nzam-almalwmat-alaytmanyh"],
        ["nzam-altmwyl-alaqary"],
        "شركات التمويل هي المركز، والتمويل العقاري فقط إن كان التمويل عقارياً.",
    ),
    _case(
        "finance_insolvency",
        "شركة تأمين رفضت مطالبة تأمين صحي رغم وجود وثيقة، والعميل يطعن في الاستثناءات.",
        ["nzam-mraqbh-shrkat-altamyn-altaawny", "nzam-aldman-alshy-altaawny"],
        ["civil-transactions-law", "law-of-evidence"],
        [],
        ["labor-law"],
        "التأمين التعاوني والضمان الصحي، مع العقد والإثبات.",
    ),
    _case(
        "finance_insolvency",
        "عميل أدرج خطأ في سجل ائتماني وأضر ذلك بطلب تمويله، ويريد التصحيح والتعويض.",
        ["nzam-almalwmat-alaytmanyh"],
        ["personal-data-protection-law", "pdpl-implementing-regulation"],
        ["civil-transactions-law"],
        ["nzam-alswq-almalyh"],
        "المعلومات الائتمانية وحقوق البيانات الشخصية.",
    ),
    _case(
        "finance_insolvency",
        "وسيط عقاري لاحظ عملية شراء عقار بمبالغ نقدية كبيرة واشتبه في غسل أموال ولم يبلغ.",
        ["anti-money-laundering-law"],
        ["real-estate-brokerage-law"],
        ["nzam-drybh-altsrfat-alaqaryh"],
        ["nzam-almnafsh"],
        "غسل الأموال مع الوساطة العقارية كجهة نشاط.",
    ),
    _case(
        "finance_insolvency",
        "منشأة مالية مهمة تواجه اضطراباً مالياً يهدد الاستقرار وتخضع لمعالجة خاصة لا لإفلاس عادي فقط.",
        ["nzam-maaljh-almnshat-almalyh-almhmh"],
        ["nzam-aliflas"],
        ["nzam-albnk-almrkzy-alsawdy"],
        ["execution-law"],
        "المعالجة الخاصة للمنشآت المالية المهمة هي النص الخاص.",
    ),
    _case(
        "ip_media_telecom",
        "منصة نسخت دورة تدريبية رقمية ومقاطع تعليمية وباعتها دون إذن صاحب المحتوى.",
        ["copyright-law"],
        ["e-commerce-law", "ecommerce-implementing-regulation"],
        ["anti-cybercrime-law"],
        ["personal-data-protection-law"],
        "حقوق المؤلف هي المركز، والتجارة الإلكترونية عند البيع.",
    ),
    _case(
        "ip_media_telecom",
        "تطبيق يستخدم شعاراً واسماً قريبين من علامة مسجلة لمنافس ويعلن بها في المتاجر الرقمية.",
        ["nzam-alalamat-altjaryh"],
        ["qanwn-nzam-alalamat-altjaryh-ldwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"],
        ["e-commerce-law"],
        ["copyright-law"],
        "العلامات التجارية هي المركز، لا حقوق المؤلف إلا عند مصنف محمي.",
    ),
    _case(
        "ip_media_telecom",
        "شركة تقلد تصميماً صناعياً محمياً وتبيع المنتج في السوق المحلي.",
        ["nzam-braaat-alakhtraa-waltsmymat-altkhtytyh-lldarat-almtkamlh-walasnaf-alnbatyh-walnmadhj-alsnaa"],
        ["commercial-fraud-law"],
        ["product-safety-law"],
        ["nzam-alalamat-altjaryh"],
        "براءات الاختراع والتصاميم الصناعية هي المركز.",
    ),
    _case(
        "ip_media_telecom",
        "مؤثر نشر إعلاناً مرئياً مضللاً عن منتج دون الإفصاح عن أنه إعلان مدفوع.",
        ["nzam-alialam-almryy-walmsmwa", "e-commerce-law"],
        ["ecommerce-implementing-regulation", "commercial-fraud-law"],
        ["nzam-almtbwaat-walnshr"],
        ["companies-law"],
        "الإعلام المرئي والمسموع والتجارة الإلكترونية والغش للإعلان المضلل.",
    ),
    _case(
        "ip_media_telecom",
        "شركة اتصالات ترسل رسائل تسويقية مزعجة وتستخدم بيانات المشتركين دون موافقة واضحة.",
        ["communications-and-information-technology-law", "personal-data-protection-law"],
        ["pdpl-implementing-regulation"],
        [],
        ["e-commerce-law"],
        "الاتصالات وتقنية المعلومات مع حماية البيانات.",
    ),
    _case(
        "ip_media_telecom",
        "صحيفة إلكترونية نشرت مادة تتضمن اتهامات غير موثقة وصوراً خاصة دون إذن.",
        ["nzam-almtbwaat-walnshr"],
        ["nzam-alialam-almryy-walmsmwa", "personal-data-protection-law"],
        ["copyright-law"],
        ["e-commerce-law"],
        "المطبوعات والنشر والإعلام، مع البيانات إذا نشرت بيانات شخصية.",
    ),
    _case(
        "health_food_drugs",
        "مستشفى خاص ارتكب خطأ طبياً وفقد جزءاً من ملف المريض ورفض تزويده بنسخة من سجله.",
        ["alnzam-alshy", "nzam-almwssat-alshyh-alkhash", "nzam-mzawlh-almhn-alshyh"],
        ["personal-data-protection-law", "pdpl-implementing-regulation"],
        ["law-of-evidence"],
        ["e-commerce-law"],
        "الحزمة الصحية المهنية والمؤسسية، ومعها حماية بيانات المريض.",
    ),
    _case(
        "health_food_drugs",
        "شركة تسوق جهازاً طبياً منزلياً عبر الإنترنت بادعاءات علاجية غير مثبتة.",
        ["nzam-alajhzh-walmstlzmat-altbyh"],
        ["e-commerce-law", "ecommerce-implementing-regulation", "commercial-fraud-law"],
        ["product-safety-law"],
        ["copyright-law"],
        "الأجهزة الطبية مع التجارة الإلكترونية والغش في الادعاءات.",
    ),
    _case(
        "health_food_drugs",
        "منشأة تبيع أدوية ومستحضرات عشبية دون ترخيص وتعلن عنها عبر حسابات التواصل.",
        ["nzam-almnshat-walmsthdrat-alsydlanyh-walashbyh"],
        ["nzam-alhyyh-alaamh-llghdhaa-waldwaa", "e-commerce-law", "commercial-fraud-law"],
        [],
        ["nzam-almnafsh"],
        "المستحضرات الصيدلانية والعشبية والهيئة، مع التجارة عند البيع الإلكتروني.",
    ),
    _case(
        "health_food_drugs",
        "مطعم تسبب في تسمم غذائي جماعي بسبب تخزين غير آمن للغذاء وبيانات صلاحية مضللة.",
        ["nzam-alghdhaa"],
        ["nzam-alhyyh-alaamh-llghdhaa-waldwaa", "commercial-fraud-law"],
        ["product-safety-law"],
        ["e-commerce-law"],
        "الغذاء والهيئة والغش التجاري عند بيانات الصلاحية.",
    ),
    _case(
        "health_food_drugs",
        "مستحضر تجميل سبب أضراراً للمستهلكين ولا يتضمن بيانات تحذيرية صحيحة على العبوة.",
        ["nzam-mntjat-altjmyl"],
        ["nzam-alhyyh-alaamh-llghdhaa-waldwaa", "commercial-fraud-law"],
        ["product-safety-law"],
        ["nzam-alajhzh-walmstlzmat-altbyh"],
        "منتجات التجميل والهيئة والغش/السلامة.",
    ),
    _case(
        "health_food_drugs",
        "منشأة صحية تتخلص من نفايات طبية خطرة بطريقة غير نظامية وتعرض العاملين للخطر.",
        ["alnzam-almwhd-lidarh-nfayat-alraayh-alshyh-bdwl-mjls-altaawn-ldwl-alkhlyj-alarbyh"],
        ["alnzam-alshy", "nzam-albyyh"],
        ["labor-law"],
        ["nzam-idarh-alnfayat"],
        "نفايات الرعاية الصحية هي النص الخاص، مع الصحة والبيئة.",
    ),
    _case(
        "family_criminal_protection",
        "أب يطلب حضانة طفل ومنع سفره والنفقة بعد الطلاق مع نزاع على المستندات والرسائل.",
        ["personal-status-law"],
        ["law-of-sharia-procedure", "law-of-evidence"],
        ["nzam-hmayh-altfl"],
        ["labor-law"],
        "الأحوال الشخصية، ثم المرافعات والإثبات.",
    ),
    _case(
        "family_criminal_protection",
        "زوجة تطلب فسخ النكاح للضرر والنفقة الماضية وإثبات رسائل تهديد.",
        ["personal-status-law"],
        ["law-of-sharia-procedure", "law-of-evidence"],
        ["protection-from-abuse-law"],
        ["criminal-procedure-law"],
        "الأحوال الشخصية هي المركز، والحماية عند وجود إيذاء.",
    ),
    _case(
        "family_criminal_protection",
        "طفل تعرض لإيذاء داخل المنزل والمدرسة أبلغت الجهات المختصة وتطلب معرفة واجبات الحماية.",
        ["nzam-hmayh-altfl", "protection-from-abuse-law"],
        ["criminal-procedure-law"],
        ["law-of-evidence"],
        ["personal-status-law"],
        "حماية الطفل والحماية من الإيذاء، ثم الإجراءات الجزائية.",
    ),
    _case(
        "family_criminal_protection",
        "شخص تلقى رسائل ابتزاز وتهديد بنشر صور خاصة عبر تطبيق تواصل.",
        ["anti-cybercrime-law"],
        ["criminal-procedure-law", "law-of-evidence"],
        ["personal-data-protection-law"],
        ["e-commerce-law"],
        "جرائم معلوماتية وإجراءات جزائية وإثبات.",
    ),
    _case(
        "family_criminal_protection",
        "موظفة تعرضت لتحرش من مديرها في مقر العمل وتريد البلاغ والحماية وإثبات الواقعة.",
        ["nzam-mkafhh-jrymh-althrsh"],
        ["labor-law", "labor-implementing-regulation", "criminal-procedure-law", "law-of-evidence"],
        ["whistleblowers-witnesses-experts-and-victims-protection-law"],
        ["nzam-alkhdmh-almdnyh"],
        "مكافحة التحرش مع العمل والإجراءات والإثبات.",
    ),
    _case(
        "family_criminal_protection",
        "مبلغ عن فساد يخشى الانتقام ويريد معرفة الحماية النظامية للشهود والمبلغين.",
        ["whistleblowers-witnesses-experts-and-victims-protection-law"],
        ["nzam-hyyh-alrqabh-wmkafhh-alfsad"],
        ["criminal-procedure-law"],
        ["labor-law"],
        "حماية المبلغين والشهود هي المركز، ونزاهة عند فساد.",
    ),
    _case(
        "family_criminal_protection",
        "شخص قدم سندات مزورة في دعوى تجارية وأثر ذلك في الحكم.",
        ["alnzam-aljzayy-ljraym-altzwyr"],
        ["criminal-procedure-law", "law-of-evidence"],
        ["nzam-almhakm-altjaryh"],
        ["electronic-transactions-law"],
        "جرائم التزوير والإجراءات والإثبات، والمحكمة التجارية سياق النزاع.",
    ),
    _case(
        "family_criminal_protection",
        "قاصر ارتكب فعلاً جنائياً وتثار إجراءات التحقيق والمحاكمة والتدابير المناسبة لعمره.",
        ["nzam-alahdath"],
        ["criminal-procedure-law"],
        ["nzam-hmayh-altfl"],
        ["personal-status-law"],
        "نظام الأحداث هو النص الخاص مع الإجراءات الجزائية.",
    ),
    _case(
        "family_criminal_protection",
        "مستهلك تعرض لاحتيال مالي عبر رابط بنكي وهمي وسحب من حسابه، ويريد المسار الجنائي والمدني.",
        ["nzam-mkafhh-alahtyal-almaly-wkhyanh-alamanh", "anti-cybercrime-law"],
        ["criminal-procedure-law", "civil-transactions-law", "law-of-evidence"],
        ["nzam-mraqbh-albnwk"],
        ["e-commerce-law"],
        "احتيال مالي وجرائم معلوماتية، ثم المدني والإثبات للتعويض.",
    ),
    _case(
        "family_criminal_protection",
        "شخص اعتدي عليه داخل مركز رعاية ويحتاج حماية عاجلة وتعويضاً وإثباتاً للواقعة.",
        ["protection-from-abuse-law"],
        ["criminal-procedure-law", "law-of-evidence", "civil-transactions-law"],
        ["nzam-hqwq-kbyr-alsn-wraayth", "nzam-hqwq-alashkhas-dhwy-aliaaqh"],
        ["labor-law"],
        "الحماية من الإيذاء مع الإجراءات والإثبات والتعويض.",
    ),
]


def load_known_slugs() -> set[str]:
    rows = json.loads(REGULATIONS_PATH.read_text(encoding="utf-8"))
    return {str(row.get("slug") or row.get("regulation_slug") or "").strip() for row in rows}


def prepare_cases() -> list[dict[str, Any]]:
    if len(CASES) != 100:
        raise SystemExit(f"Expected exactly 100 cases, found {len(CASES)}")

    known = load_known_slugs()
    prepared: list[dict[str, Any]] = []
    for idx, row in enumerate(CASES, start=1):
        case = dict(row)
        case["question_id"] = f"gpr_v1_{idx:03d}"
        case["split"] = SPLIT_CYCLE[(idx - 1) % len(SPLIT_CYCLE)]
        case["benchmark_category"] = f"gold_package_recall_v1_{case['domain']}"
        case["question_type"] = f"package_recall_{case['split']}"
        case["expected_behavior"] = "answer"

        expected = [
            *case["required_core_regulations"],
            *case["required_companion_regulations"],
        ]
        case["expected_regulations"] = list(dict.fromkeys(expected))
        case["allowed_regulations"] = list(
            dict.fromkeys([*expected, *case.get("optional_regulations", [])])
        )
        case["min_expected_regulation_hits"] = len(case["expected_regulations"])
        case["expected_articles"] = []
        case["min_expected_article_hits"] = 0

        missing = sorted(
            slug
            for slug in [
                *case["expected_regulations"],
                *case.get("optional_regulations", []),
                *case.get("excluded_regulations", []),
            ]
            if slug not in known
        )
        if missing:
            raise SystemExit(f"{case['question_id']} has unknown regulation slugs: {missing}")
        prepared.append(case)
    return prepared


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    cases = prepare_cases()
    write_jsonl(CASES_PATH, cases)

    split_counts = Counter(case["split"] for case in cases)
    domain_counts = Counter(case["domain"] for case in cases)
    manifest = {
        "benchmark_id": "gold_package_recall_100_v1",
        "purpose": "Frozen Saudi legal RAG package-recall benchmark; gold is never sent to the service.",
        "cases_path": str(CASES_PATH.relative_to(ROOT)),
        "case_count": len(cases),
        "split_counts": dict(sorted(split_counts.items())),
        "domain_counts": dict(sorted(domain_counts.items())),
        "retrieval_profile": "jamia_recall",
        "answer_mode": "benchmark",
        "anti_leakage_rules": [
            "Do not import this file from app/rag or ingestion code.",
            "Do not copy gold packages into routing bundles case-by-case.",
            "Use dev for diagnosis, regression for gate, and heldout only for final confirmation.",
            "The runner sends only the question text to /internal/rag/query.",
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    README_PATH.write_text(
        "# Gold Package Recall 100 v1\n\n"
        "هذا معيار ذهبي ثابت لقياس جمع الحزمة النظامية في RAG القانوني السعودي.\n\n"
        "- عدد الحالات: 100.\n"
        "- الهدف: قياس هل استُدعي النظام الحاكم واللائحة/الضوابط المرافقة.\n"
        "- لا تُرسل الإجابات الذهبية إلى الخدمة؛ يرسل runner نص السؤال فقط.\n"
        "- في مرحلة الجمع لا نعاقب كل مصدر زائد، لكن نسجل `excluded_regulations` كمصائد خطرة.\n"
        "- التقسيم: dev للتشخيص، regression للبوابة، heldout للتأكيد النهائي.\n\n"
        "تشغيل مقترح:\n\n"
        "```bash\n"
        "python3 scripts/run_gold_package_recall.py --split dev\n"
        "python3 scripts/run_gold_package_recall.py --split regression\n"
        "python3 scripts/run_gold_package_recall.py --split heldout\n"
        "```\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
