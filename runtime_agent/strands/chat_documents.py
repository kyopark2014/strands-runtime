# Copyright 2026 Amazon.com, Inc. or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Document loading and summarization helpers for the chat module."""

import base64
import csv
import logging
import traceback
import uuid
from io import BytesIO
from urllib import parse

import PyPDF2
from PIL import Image
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter

from chat_s3 import get_s3_client, get_s3_resource

logger = logging.getLogger("chat")

SUMMARY_MAX_CHARS = 500
MIN_EXTRACTED_TEXT_LENGTH = 10
MAX_LLM_RETRY_ATTEMPTS = 5

# Cap decoded image dimensions before Bedrock multimodal encode: keeps memory and
# base64 payload size bounded (Bedrock image input practical limit ~2M pixels).
MAX_IMAGE_PIXELS = 2_000_000

IMAGE_FILE_TYPES = ("png", "jpeg", "jpg", "webp", "gif")
DOCUMENT_FILE_TYPES = ("pdf", "txt", "md", "pptx", "docx")


class DocumentSummaryService:
    """LLM-based text summarization for document content."""

    def build_prompt(self, text: str) -> ChatPromptTemplate:
        import chat

        if chat.isKorean(text) == True:
            system = (
                f"다음의 <article> tag안의 문장을 요약해서 {SUMMARY_MAX_CHARS}자 이내로 설명하세오."
            )
        else:
            system = (
                "Here is pieces of article, contained in <article> tags. "
                f"Write a concise summary within {SUMMARY_MAX_CHARS} characters."
            )

        human = "<article>{text}</article>"
        return ChatPromptTemplate.from_messages([("system", system), ("human", human)])

    def invoke(self, docs) -> str:
        import chat

        llm = chat.get_chat(extended_thinking=chat.reasoning_mode)

        text = ""
        for doc in docs:
            text = text + doc

        prompt = self.build_prompt(text)
        chain = prompt | llm
        try:
            result = chain.invoke({"text": text})

            summary = result.content
            logger.info(f"esult of summarization: {summary}")
        except Exception:
            err_msg = traceback.format_exc()
            logger.info(f"error message: {err_msg}")
            raise Exception("Not able to request to LLM")

        return summary


class DocumentLoaderService:
    """S3 document retrieval, format parsing, and text splitting."""

    def fetch_s3_object(self, s3_file_name):
        import chat

        s3r = get_s3_resource()
        doc = s3r.Object(chat.s3_bucket, chat.s3_prefix + "/" + s3_file_name)
        logger.info(
            f"s3_bucket: {chat.s3_bucket}, s3_prefix: {chat.s3_prefix}, "
            f"s3_file_name: {s3_file_name}"
        )
        return doc

    def parse_pdf(self, raw_bytes: bytes) -> str:
        reader = PyPDF2.PdfReader(BytesIO(raw_bytes))

        raw_text = []
        for page in reader.pages:
            raw_text.append(page.extract_text())
        return "\n".join(raw_text)

    def parse_text_file(self, raw_bytes: bytes) -> str:
        return raw_bytes.decode("utf-8")

    def split_text(self, contents: str) -> list:
        logger.info(f"contents: {contents}")
        new_contents = str(contents).replace("\n", " ")
        logger.info(f"length: {len(new_contents)}")

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=100,
            separators=["\n\n", "\n", ".", " ", ""],
            length_function=len,
        )
        texts = text_splitter.split_text(new_contents)
        if texts:
            logger.info(f"exts[0]: {texts[0]}")

        return texts

    def load(self, file_type, s3_file_name):
        doc = self.fetch_s3_object(s3_file_name)

        contents = ""
        if file_type == "pdf":
            contents = self.parse_pdf(doc.get()["Body"].read())
        elif file_type == "txt" or file_type == "md":
            contents = self.parse_text_file(doc.get()["Body"].read())

        return self.split_text(contents)

    def load_csv(self, s3_file_name):
        doc = self.fetch_s3_object(s3_file_name)

        lines = doc.get()["Body"].read().decode("utf-8").split("\n")  # read csv per line
        logger.info(f"lins: {len(lines)}")

        columns = lines[0].split(",")  # get columns
        logger.info(f"columns: {columns}")

        docs = []
        n = 0
        for row in csv.DictReader(lines, delimiter=",", quotechar='"'):
            values = {k: row[k] for k in columns if k in row}
            content = "\n".join(f"{k.strip()}: {v.strip()}" for k, v in values.items())
            doc = Document(
                page_content=content,
                metadata={
                    "name": s3_file_name,
                    "row": n + 1,
                }
            )
            docs.append(doc)
            n = n + 1
        logger.info(f"docs[0]: {docs[0]}")

        return docs


_summary_service = DocumentSummaryService()
_loader_service = DocumentLoaderService()


def get_summary(docs):
    return _summary_service.invoke(docs)


def load_document(file_type, s3_file_name):
    return _loader_service.load(file_type, s3_file_name)


def _file_name_from_ref(file_ref: str) -> str:
    """Extract a basename from a sharing URL or plain file name."""
    raw = (file_ref or "").strip()
    if not raw:
        return ""
    name = raw.rsplit("/", 1)[-1]
    return parse.unquote(name)


def summarize_image_file(file_name, prompt=""):
    import chat

    s3_client = get_s3_client()
    s3_key = f"{chat.s3_image_prefix}/{file_name}"
    logger.info(f"loading image from s3://{chat.s3_bucket}/{s3_key}")
    try:
        image_obj = s3_client.get_object(Bucket=chat.s3_bucket, Key=s3_key)
        image_content = image_obj["Body"].read()
    except Exception:
        logger.exception("Failed to load image from S3 key=%s", s3_key)
        return "이미지 파일을 불러오지 못했습니다."

    image_summary_prompt = (
        "사용자의 요청을 참조하여 이미지의 내용을 분석한 후에 markdown 포맷으로 "
        "자세히 설명해주세요. 사용자 요청: <user_request>"
        f"{prompt}</user_request>"
    )
    logger.info(f"image_summary_prompt: {image_summary_prompt}")

    return summarize_image(image_content, image_summary_prompt)


def summarize_csv_file(file_name):
    docs = load_csv_document(file_name)
    contexts = []
    for doc in docs:
        contexts.append(doc.page_content)
    logger.info(f"contexts: {contexts}")

    return get_summary(contexts)


def summarize_document_file(file_name, file_type):
    import chat

    texts = load_document(file_type, file_name)

    if len(texts):
        docs = []
        for i in range(len(texts)):
            docs.append(
                Document(
                    page_content=texts[i],
                    metadata={
                        "name": file_name,
                        # 'page':i+1,
                        "url": chat.path
                        + "/"
                        + chat.doc_prefix
                        + parse.quote(file_name),
                    },
                )
            )
        logger.info(f"docs[0]: {docs[0]}")
        logger.info(f"docs size: {len(docs)}")

        contexts = []
        for doc in docs:
            contexts.append(doc.page_content)
        logger.info(f"contexts: {contexts}")

        return get_summary(contexts)

    return "문서 로딩에 실패하였습니다."


def get_summary_of_uploaded_file(file_ref, st=None, prompt=""):
    """Analyze an uploaded file (by URL or name) and return a text summary.

    Images are loaded from S3 under images/ and summarized with vision.
    Documents keep the existing docs/ load path.
    """
    import chat

    file_name = _file_name_from_ref(file_ref)
    if not file_name:
        return "파일 이름을 확인할 수 없습니다."

    file_type = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    logger.info(
        f"get_summary_of_uploaded_file: file_name={file_name}, file_type={file_type}"
    )

    if file_type in IMAGE_FILE_TYPES:
        return summarize_image_file(file_name, prompt)

    msg = "지원하지 않는 파일 형식입니다."

    if file_type == "csv":
        msg = summarize_csv_file(file_name)
    elif file_type in DOCUMENT_FILE_TYPES:
        msg = summarize_document_file(file_name, file_type)

    chat.fileId = uuid.uuid4().hex

    return msg


def load_csv_document(s3_file_name):
    return _loader_service.load_csv(s3_file_name)


def _resize_and_encode(image_content):
    """Resize image if needed and return base64-encoded string."""
    img = Image.open(BytesIO(image_content))
    width, height = img.size
    logger.info(f"width: {width}, height: {height}, size: {width*height}")

    isResized = False
    max_size = 5 * 1024 * 1024  # 5MB

    while width * height > MAX_IMAGE_PIXELS:
        width = int(width / 2)
        height = int(height / 2)
        isResized = True

    if isResized:
        img = img.resize((width, height))

    max_attempts = 5
    img_base64 = ""
    base64_size = 0
    for attempt in range(max_attempts):
        buffer = BytesIO()
        img.save(buffer, format="PNG", optimize=True)
        img_bytes = buffer.getvalue()
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")

        base64_size = len(img_base64.encode("utf-8"))
        logger.info(f"attempt {attempt + 1}: base64_size = {base64_size} bytes")

        if base64_size <= max_size:
            break
        width = int(width * 0.8)
        height = int(height * 0.8)
        img = img.resize((width, height))
        logger.info(f"resizing to {width}x{height} due to size limit")

    if base64_size > max_size:
        raise Exception("이미지 크기가 너무 큽니다. 5MB 이하의 이미지를 사용해주세요.")

    return img_base64


def extract_text(img_base64):
    """Extract text from an image using multimodal LLM."""
    import chat

    multimodal = chat.get_chat(extended_thinking=chat.reasoning_mode)
    query = "텍스트를 추출해서 markdown 포맷으로 변환하세요. <result> tag를 붙여주세요."

    messages = [
        HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_base64}",
                    },
                },
                {"type": "text", "text": query},
            ]
        )
    ]

    extracted_text = ""
    for attempt in range(MAX_LLM_RETRY_ATTEMPTS):
        logger.info(f"extract_text attempt: {attempt}")
        try:
            result = multimodal.invoke(messages)
            extracted_text = result.content
            break
        except Exception:
            err_msg = traceback.format_exc()
            logger.info(f"error message: {err_msg}")

    logger.info(f"extracted_text: {extracted_text}")
    if len(extracted_text) < MIN_EXTRACTED_TEXT_LENGTH:
        extracted_text = "텍스트를 추출하지 못하였습니다."

    return extracted_text


def summary_image(img_base64, instruction):
    """Summarize an image using multimodal LLM."""
    import chat

    llm = chat.get_chat(extended_thinking=chat.reasoning_mode)

    if instruction:
        logger.info(f"instruction: {instruction}")
        query = f"{instruction}. <result> tag를 붙여주세요. 한국어로 답변하세요."
    else:
        query = (
            "이미지가 의미하는 내용을 풀어서 자세히 알려주세요. "
            "markdown 포맷으로 답변을 작성합니다."
        )

    messages = [
        HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_base64}",
                    },
                },
                {"type": "text", "text": query},
            ]
        )
    ]

    for attempt in range(MAX_LLM_RETRY_ATTEMPTS):
        logger.info(f"summary_image attempt: {attempt}")
        try:
            result = llm.invoke(messages)
            extracted_text = result.content
            break
        except Exception:
            err_msg = traceback.format_exc()
            logger.info(f"error message: {err_msg}")
            raise Exception("Not able to request to LLM")

    return extracted_text


def summarize_image(image_content: bytes, prompt: str) -> str:
    """Resize image and summarize with vision."""
    img_base64 = _resize_and_encode(image_content)

    logger.info("이미지의 내용을 분석합니다.")
    result = summary_image(img_base64, prompt)

    summary = result[result.find("<result>") + 8 : result.find("</result>")]
    logger.info(f"image summary: {summary}")

    return summary
