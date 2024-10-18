import os
import ffmpeg
import logging
import requests
import subprocess
from services.file_management import download_file
from services.gcp_toolkit import upload_to_gcs, GCP_BUCKET_NAME

# Set the default local storage directory
STORAGE_PATH = "/tmp/"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define the path to the fonts directory
FONTS_DIR = '/usr/share/fonts/custom'

# Create the FONT_PATHS dictionary by reading the fonts directory
FONT_PATHS = {}
for font_file in os.listdir(FONTS_DIR):
    if font_file.endswith('.ttf') or font_file.endswith('.TTF'):
        font_name = os.path.splitext(font_file)[0]
        FONT_PATHS[font_name] = os.path.join(FONTS_DIR, font_file)

# Create a list of acceptable font names
ACCEPTABLE_FONTS = list(FONT_PATHS.keys())

def match_fonts():
    try:
        result = subprocess.run(['fc-list', ':family'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            fontconfig_fonts = result.stdout.split('\n')
            fontconfig_fonts = list(set(fontconfig_fonts))
            matched_fonts = {}
            for font_file in FONT_PATHS.keys():
                for fontconfig_font in fontconfig_fonts:
                    if font_file.lower() in fontconfig_font.lower():
                        matched_fonts[font_file] = fontconfig_font.strip()

            unique_font_names = set()
            for font in matched_fonts.values():
                font_name = font.split(':')[1].strip()
                unique_font_names.add(font_name)
            
            unique_font_names = sorted(list(set(unique_font_names)))
            
            for font_name in unique_font_names:
                print(font_name)
        else:
            logger.error(f"Error matching fonts: {result.stderr}")
    except Exception as e:
        logger.error(f"Exception while matching fonts: {str(e)}")

def generate_style_line(options):
    """Generate ASS style line from options."""
    style_options = {
        'Name': 'Default',
        'Fontname': options.get('font_name', 'Arial'),
        'Fontsize': options.get('font_size', 24),
        'PrimaryColour': options.get('primary_color', '&H00FFFFFF'),
        'OutlineColour': options.get('outline_color', '&H00000000'),
        'BackColour': options.get('back_color', '&H00000000'),
        'Bold': options.get('bold', 0),
        'Italic': options.get('italic', 0),
        'Underline': options.get('underline', 0),
        'StrikeOut': options.get('strikeout', 0),
        'ScaleX': 100,
        'ScaleY': 100,
        'Spacing': 0,
        'Angle': 0,
        'BorderStyle': 1,
        'Outline': options.get('outline', 1),
        'Shadow': options.get('shadow', 0),
        'Alignment': options.get('alignment', 2),
        'MarginL': options.get('margin_l', 10),
        'MarginR': options.get('margin_r', 10),
        'MarginV': options.get('margin_v', 10),
        'Encoding': options.get('encoding', 1)
    }
    return f"Style: {','.join(str(v) for v in style_options.values())}"

def process_single_word_caption(caption_text, start_time, end_time, highlight_color, regular_color):
    """Process a caption line to create individual word timing for ASS format."""
    words = caption_text.strip().split()
    total_duration = end_time - start_time
    word_duration = total_duration / len(words)
    
    ass_lines = []
    for i, word in enumerate(words):
        word_start = start_time + (i * word_duration)
        word_end = word_start + word_duration
        
        # Format timecodes as h:mm:ss.cc
        start_str = f"{int(word_start/3600)}:{int((word_start%3600)/60):02d}:{word_start%60:05.2f}"
        end_str = f"{int(word_end/3600)}:{int((word_end%3600)/60):02d}:{word_end%60:05.2f}"
        
        # Create ASS line with highlighted word
        other_words = words.copy()
        other_words[i] = f"{{\c&H{highlight_color}&}}{word}{{\c&H{regular_color}&}}"
        line_text = " ".join(other_words)
        
        ass_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{line_text}")
    
    return ass_lines

def convert_srt_to_one_word_ass(srt_content, options):
    """Convert SRT content to ASS format with one-word-at-a-time highlighting."""
    lines = srt_content.strip().split('\n\n')
    ass_lines = []
    
    highlight_color = options.get('highlight_color', 'FFFFFF')  # Default: white
    regular_color = options.get('regular_color', 'AAAAAA')     # Default: light gray
    
    for line in lines:
        parts = line.split('\n')
        if len(parts) >= 3:
            # Parse timecode
            timecode = parts[1].split(' --> ')
            start_time = sum(float(x) * y for x, y in zip(timecode[0].replace(',', '.').split(':')[::-1], [1, 60, 3600]))
            end_time = sum(float(x) * y for x, y in zip(timecode[1].replace(',', '.').split(':')[::-1], [1, 60, 3600]))
            
            # Get caption text (might be multiple lines)
            caption_text = ' '.join(parts[2:])
            
            # Process caption text into word-by-word ASS lines
            ass_lines.extend(process_single_word_caption(
                caption_text, 
                start_time, 
                end_time,
                highlight_color,
                regular_color
            ))
    
    return '\n'.join(ass_lines)

def process_captioning(file_url, caption_srt, caption_type, options, job_id):
    """Process video captioning using FFmpeg."""
    try:
        logger.info(f"Job {job_id}: Starting download of file from {file_url}")
        video_path = download_file(file_url, STORAGE_PATH)
        logger.info(f"Job {job_id}: File downloaded to {video_path}")

        subtitle_extension = '.' + caption_type
        srt_path = os.path.join(STORAGE_PATH, f"{job_id}{subtitle_extension}")
        options = convert_array_to_collection(options)
        caption_style = ""

        # Handle one-word-at-a-time caption mode
        one_word_mode = options.get('one_word_mode', False)
        if one_word_mode and caption_type != 'ass':
            caption_type = 'ass'
            subtitle_extension = '.ass'
            srt_path = os.path.join(STORAGE_PATH, f"{job_id}{subtitle_extension}")
            logger.info(f"Job {job_id}: One-word mode enabled, converting to ASS format")

        if caption_type == 'ass':
            style_string = generate_style_line(options)
            caption_style = f"""
[Script Info]
Title: Highlight Current Word
ScriptType: v4.00+
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_string}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
            logger.info(f"Job {job_id}: Generated ASS style string: {style_string}")

        if caption_srt.startswith("https"):
            logger.info(f"Job {job_id}: Downloading caption file from {caption_srt}")
            response = requests.get(caption_srt)
            response.raise_for_status()
            
            if one_word_mode:
                # Convert SRT content to one-word ASS format
                subtitle_content = caption_style + convert_srt_to_one_word_ass(response.text, options)
                with open(srt_path, 'w') as srt_file:
                    srt_file.write(subtitle_content)
            else:
                if caption_type in ['srt','vtt']:
                    with open(srt_path, 'wb') as srt_file:
                        srt_file.write(response.content)
                else:
                    subtitle_content = caption_style + response.text
                    with open(srt_path, 'w') as srt_file:
                        srt_file.write(subtitle_content)
            logger.info(f"Job {job_id}: Caption file processed and saved to {srt_path}")
        else:
            if one_word_mode:
                # Convert direct SRT content to one-word ASS format
                subtitle_content = caption_style + convert_srt_to_one_word_ass(caption_srt, options)
            else:
                subtitle_content = caption_style + caption_srt
            
            with open(srt_path, 'w') as srt_file:
                srt_file.write(subtitle_content)
            logger.info(f"Job {job_id}: Caption file created at {srt_path}")

        output_path = os.path.join(STORAGE_PATH, f"{job_id}_captioned.mp4")
        logger.info(f"Job {job_id}: Output path set to {output_path}")

        font_name = options.get('font_name', 'Arial')
        if font_name in FONT_PATHS:
            selected_font = FONT_PATHS[font_name]
            logger.info(f"Job {job_id}: Font path set to {selected_font}")
        else:
            selected_font = FONT_PATHS.get('Arial')
            logger.warning(f"Job {job_id}: Font {font_name} not found. Using default font Arial.")

        if subtitle_extension == '.ass':
            subtitle_filter = f"subtitles='{srt_path}'"
            logger.info(f"Job {job_id}: Using ASS subtitle filter: {subtitle_filter}")
        else:
            subtitle_filter = f"subtitles={srt_path}:force_style='"
            style_options = {
                'FontName': font_name,
                'FontSize': options.get('font_size', 24),
                'PrimaryColour': options.get('primary_color', '&H00FFFFFF'),
                'SecondaryColour': options.get('secondary_color', '&H00000000'),
                'OutlineColour': options.get('outline_color', '&H00000000'),
                'BackColour': options.get('back_color', '&H00000000'),
                'Bold': options.get('bold', 0),
                'Italic': options.get('italic', 0),
                'Underline': options.get('underline', 0),
                'StrikeOut': options.get('strikeout', 0),
                'Alignment': options.get('alignment', 2),
                'MarginV': options.get('margin_v', 10),
                'MarginL': options.get('margin_l', 10),
                'MarginR': options.get('margin_r', 10),
                'Outline': options.get('outline', 1),
                'Shadow': options.get('shadow', 0),
                'Blur': options.get('blur', 0),
                'BorderStyle': options.get('border_style', 1),
                'Encoding': options.get('encoding', 1),
                'Spacing': options.get('spacing', 0),
                'Angle': options.get('angle', 0),
                'UpperCase': options.get('uppercase', 0)
            }

            subtitle_filter += ','.join(f"{k}={v}" for k, v in style_options.items() if v is not None)
            subtitle_filter += "'"
            logger.info(f"Job {job_id}: Using subtitle filter: {subtitle_filter}")

        try:
            logger.info(f"Job {job_id}: Running FFmpeg with filter: {subtitle_filter}")
            ffmpeg.input(video_path).output(
                output_path,
                vf=subtitle_filter,
                acodec='copy'
            ).run()
            logger.info(f"Job {job_id}: FFmpeg processing completed, output file at {output_path}")
        except ffmpeg.Error as e:
            if e.stderr:
                error_message = e.stderr.decode('utf8')
            else:
                error_message = 'Unknown FFmpeg error'
            logger.error(f"Job {job_id}: FFmpeg error: {error_message}")
            raise

        output_filename = upload_to_gcs(output_path, GCP_BUCKET_NAME)
        logger.info(f"Job {job_id}: File uploaded to GCS at {output_filename}")

        os.remove(video_path)
        os.remove(srt_path)
        os.remove(output_path)
        logger.info(f"Job {job_id}: Local files cleaned up")
        return output_filename
    except Exception as e:
        logger.error(f"Job {job_id}: Error in process_captioning: {str(e)}")
        raise

def convert_array_to_collection(options):
    logger.info(f"Converting options array to dictionary: {options}")
    return {item["option"]: item["value"] for item in options}
